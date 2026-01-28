"""
Security middleware and utilities for Autowrkers

Provides:
- Rate limiting
- Security headers
- CORS configuration
- HTTPS redirect
- Path validation
- Input sanitization
"""
import os
import re
import time
from collections import defaultdict
from typing import Optional, Set, Dict, Callable, Any, List
from functools import wraps

from fastapi import Request, HTTPException
from fastapi.responses import Response, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .logging_config import get_logger

logger = get_logger("autowrkers.security")


# ==================== Rate Limiting ====================

class RateLimiter:
    """
    Simple in-memory rate limiter.

    Tracks requests per IP address with configurable limits.
    """

    def __init__(self):
        # IP -> list of request timestamps
        self._requests: Dict[str, list] = defaultdict(list)
        # IP -> block until timestamp
        self._blocked: Dict[str, float] = {}
        # Violation count per IP
        self._violations: Dict[str, int] = defaultdict(int)

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP from request, respecting X-Forwarded-For."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Take the first IP in the chain
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup_old_requests(self, ip: str, window: int):
        """Remove requests older than the window."""
        cutoff = time.time() - window
        self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

    def is_blocked(self, ip: str) -> bool:
        """Check if an IP is currently blocked."""
        if ip in self._blocked:
            if time.time() < self._blocked[ip]:
                return True
            else:
                del self._blocked[ip]
        return False

    def block_ip(self, ip: str, duration: int = 300):
        """Block an IP for a duration (default 5 minutes)."""
        self._blocked[ip] = time.time() + duration
        logger.warning(f"Blocked IP {ip} for {duration} seconds")

    def check_rate_limit(
        self,
        request: Request,
        limit: int,
        window: int = 60
    ) -> bool:
        """
        Check if request is within rate limit.

        Args:
            request: FastAPI request
            limit: Max requests per window
            window: Time window in seconds

        Returns:
            True if allowed, False if rate limited
        """
        ip = self._get_client_ip(request)

        # Check if blocked
        if self.is_blocked(ip):
            return False

        # Cleanup old requests
        self._cleanup_old_requests(ip, window)

        # Check limit
        if len(self._requests[ip]) >= limit:
            self._violations[ip] += 1
            # Block after 3 violations
            if self._violations[ip] >= 3:
                self.block_ip(ip)
            return False

        # Record this request
        self._requests[ip].append(time.time())
        return True

    def get_remaining(self, request: Request, limit: int, window: int = 60) -> int:
        """Get remaining requests for an IP."""
        ip = self._get_client_ip(request)
        self._cleanup_old_requests(ip, window)
        return max(0, limit - len(self._requests[ip]))


# Global rate limiter instance
rate_limiter = RateLimiter()


# Rate limit configurations
RATE_LIMITS = {
    "default": (100, 60),      # 100 requests per minute
    "auth": (10, 60),          # 10 auth attempts per minute
    "sensitive": (30, 60),     # 30 sensitive operations per minute
    "websocket": (10, 60),     # 10 WebSocket connections per minute
}


def rate_limit(category: str = "default"):
    """
    Decorator to apply rate limiting to an endpoint.

    Usage:
        @app.get("/api/resource")
        @rate_limit("default")
        async def get_resource():
            ...
    """
    limit, window = RATE_LIMITS.get(category, RATE_LIMITS["default"])

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Find the Request object in args or kwargs
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if not request:
                request = kwargs.get("request")

            if request and not rate_limiter.check_rate_limit(request, limit, window):
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please slow down.",
                    headers={"Retry-After": str(window)}
                )

            return await func(*args, **kwargs)
        return wrapper
    return decorator


# ==================== Security Headers Middleware ====================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to all responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # XSS protection (legacy, but still useful)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy
        # Allow inline scripts/styles for the web UI, but restrict other sources
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self'",
            "connect-src 'self' ws: wss:",
            "frame-ancestors 'none'",
            "form-action 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        # Permissions Policy
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )

        return response


# ==================== HTTPS Redirect Middleware ====================

class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Redirects HTTP requests to HTTPS when SSL is configured."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only redirect if the request came in over HTTP
        # Check X-Forwarded-Proto for reverse proxy setups
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto == "http":
            url = request.url.replace(scheme="https")
            return RedirectResponse(url=str(url), status_code=301)
        return await call_next(request)


# ==================== CORS Configuration ====================

def get_cors_origins() -> List[str]:
    """
    Get allowed CORS origins from environment variable.

    Set AUTOWRKERS_CORS_ORIGINS to a comma-separated list of allowed origins.
    Defaults to localhost origins only.

    Examples:
        AUTOWRKERS_CORS_ORIGINS=https://myapp.example.com
        AUTOWRKERS_CORS_ORIGINS=https://app1.com,https://app2.com
        AUTOWRKERS_CORS_ORIGINS=*   (allow all - NOT recommended for production)
    """
    env_origins = os.environ.get("AUTOWRKERS_CORS_ORIGINS", "").strip()
    if env_origins:
        return [o.strip() for o in env_origins.split(",") if o.strip()]
    # Default: localhost only
    return [
        "http://localhost:8420",
        "http://127.0.0.1:8420",
        "https://localhost:8420",
        "https://127.0.0.1:8420",
    ]


# ==================== Rate Limiting Middleware ====================

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies rate limiting to all requests."""

    # Paths with stricter rate limits
    AUTH_PATHS = {"/api/auth/login", "/api/auth/register", "/api/auth/refresh"}
    SENSITIVE_PATHS = {
        "/api/sessions",
        "/api/projects",
        "/api/workflow/execute",
        "/api/daemon/install",
    }

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Determine rate limit category
        if path in self.AUTH_PATHS:
            category = "auth"
        elif any(path.startswith(p) for p in self.SENSITIVE_PATHS):
            category = "sensitive"
        else:
            category = "default"

        limit, window = RATE_LIMITS.get(category, RATE_LIMITS["default"])

        if not rate_limiter.check_rate_limit(request, limit, window):
            return Response(
                content='{"detail": "Too many requests. Please slow down."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(window)}
            )

        response = await call_next(request)

        # Add rate limit headers
        remaining = rate_limiter.get_remaining(request, limit, window)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(window)

        return response


# ==================== Path Validation ====================

# Allowed base directories for file operations
ALLOWED_BASE_PATHS: Set[str] = {
    os.path.expanduser("~"),  # User's home directory
    "/tmp",
    "/var/tmp",
}


def validate_path(user_path: str, allowed_base: Optional[str] = None) -> str:
    """
    Validate that a path is within allowed directories.

    Prevents path traversal attacks.

    Args:
        user_path: The user-supplied path
        allowed_base: Optional specific base to validate against

    Returns:
        The validated, resolved path

    Raises:
        HTTPException: If path is outside allowed directories
    """
    # Expand user paths like ~
    expanded = os.path.expanduser(user_path)

    # Resolve to absolute path, removing .. etc
    real_path = os.path.realpath(expanded)

    if allowed_base:
        # Check against specific base
        real_base = os.path.realpath(os.path.expanduser(allowed_base))
        if not real_path.startswith(real_base + os.sep) and real_path != real_base:
            logger.warning(f"Path traversal attempt: {user_path} -> {real_path}")
            raise HTTPException(
                status_code=400,
                detail="Invalid path: access denied"
            )
    else:
        # Check against allowed base paths
        is_allowed = any(
            real_path.startswith(base + os.sep) or real_path == base
            for base in ALLOWED_BASE_PATHS
        )
        if not is_allowed:
            logger.warning(f"Path traversal attempt: {user_path} -> {real_path}")
            raise HTTPException(
                status_code=400,
                detail="Invalid path: access denied"
            )

    return real_path


def is_safe_path(user_path: str, allowed_base: Optional[str] = None) -> bool:
    """
    Check if a path is safe without raising an exception.

    Returns:
        True if path is safe, False otherwise
    """
    try:
        validate_path(user_path, allowed_base)
        return True
    except HTTPException:
        return False


# ==================== Input Validation ====================

# Regex patterns for common validations
PATTERNS = {
    "github_repo": re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$"),
    "branch_name": re.compile(r"^[a-zA-Z0-9._/-]+$"),
    "username": re.compile(r"^[a-zA-Z0-9_-]{3,32}$"),
    "email": re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"),
}


def validate_input(value: str, pattern_name: str, max_length: int = 255) -> bool:
    """
    Validate input against a named pattern.

    Args:
        value: The value to validate
        pattern_name: Name of the pattern to use
        max_length: Maximum allowed length

    Returns:
        True if valid, False otherwise
    """
    if not value or len(value) > max_length:
        return False

    pattern = PATTERNS.get(pattern_name)
    if not pattern:
        return False

    return bool(pattern.match(value))


def sanitize_string(value: str, max_length: int = 1000) -> str:
    """
    Sanitize a string by removing potentially dangerous characters.

    Removes null bytes and control characters, trims to max length.
    """
    if not value:
        return ""

    # Remove null bytes
    value = value.replace('\x00', '')

    # Remove other control characters except newlines and tabs
    value = ''.join(
        c for c in value
        if c in '\n\r\t' or (ord(c) >= 32 and ord(c) != 127)
    )

    # Trim to max length
    return value[:max_length]


# ==================== SQL Injection Prevention ====================

# Whitelist of allowed column names for dynamic queries
ALLOWED_PROJECT_FIELDS = {
    'id', 'name', 'github_repo', 'working_dir', 'default_branch',
    'auto_sync', 'auto_start', 'status', 'created_at', 'last_sync',
    'llm_provider', 'llm_model'
}

ALLOWED_SESSION_FIELDS = {
    'id', 'project_id', 'github_issue_number', 'status',
    'branch_name', 'created_at', 'started_at', 'completed_at'
}

ALLOWED_WORKFLOW_FIELDS = {
    'id', 'template_id', 'status', 'project_id', 'created_at',
    'started_at', 'completed_at', 'trigger_mode'
}


def validate_field_name(field: str, allowed_fields: Set[str]) -> str:
    """
    Validate a field name against a whitelist.

    Args:
        field: The field name to validate
        allowed_fields: Set of allowed field names

    Returns:
        The validated field name

    Raises:
        ValueError: If field is not in the whitelist
    """
    if field not in allowed_fields:
        raise ValueError(f"Invalid field name: {field}")
    return field
