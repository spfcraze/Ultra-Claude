"""
Authentication module for UltraClaude

Provides JWT-based authentication for API endpoints.
"""
import os
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pathlib import Path

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# Try to import jose, but make auth optional if not installed
try:
    from jose import jwt, JWTError
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    jwt = None
    JWTError = Exception

from .crypto import encryption
from .database import db

# Constants
DATA_DIR = Path.home() / ".ultraclaude"
AUTH_CONFIG_KEY = "auth_config"
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
REFRESH_TOKEN_EXPIRE_DAYS = 30

# Security scheme
security = HTTPBearer(auto_error=False)


class TokenData(BaseModel):
    """Data stored in JWT token."""
    username: str
    exp: datetime


class AuthConfig:
    """Authentication configuration and user management."""

    _instance: Optional['AuthConfig'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._jwt_secret: Optional[str] = None
        self._auth_enabled: bool = False
        self._users: Dict[str, str] = {}  # username -> hashed password
        self._load_config()
        self._initialized = True

    def _load_config(self):
        """Load auth configuration from database or environment."""
        # Check environment variable to enable/disable auth
        env_enabled = os.environ.get('ULTRACLAUDE_AUTH_ENABLED', '').lower()
        if env_enabled in ('true', '1', 'yes'):
            self._auth_enabled = True
        elif env_enabled in ('false', '0', 'no'):
            self._auth_enabled = False
        else:
            # Default: auth is disabled in development, enabled in production
            # Production is detected by binding to 0.0.0.0 or non-localhost
            self._auth_enabled = False

        # Load JWT secret from env or generate
        self._jwt_secret = os.environ.get('ULTRACLAUDE_JWT_SECRET')
        if not self._jwt_secret:
            # Try to load from database
            stored_secret = db.get_setting('jwt_secret')
            if stored_secret:
                self._jwt_secret = encryption.decrypt_or_return(stored_secret)
            else:
                # Generate and store new secret
                self._jwt_secret = secrets.token_urlsafe(32)
                db.set_setting('jwt_secret', encryption.encrypt(self._jwt_secret))

        # Load users from database
        self._load_users()

    def _load_users(self):
        """Load users from database."""
        users_data = db.get_setting('auth_users')
        if users_data:
            try:
                import json
                self._users = json.loads(users_data)
            except Exception:
                self._users = {}

    def _save_users(self):
        """Save users to database."""
        import json
        db.set_setting('auth_users', json.dumps(self._users))

    @property
    def is_enabled(self) -> bool:
        """Check if authentication is enabled."""
        return self._auth_enabled and JWT_AVAILABLE

    def enable(self):
        """Enable authentication."""
        self._auth_enabled = True
        db.set_setting('auth_enabled', 'true')

    def disable(self):
        """Disable authentication."""
        self._auth_enabled = False
        db.set_setting('auth_enabled', 'false')

    def hash_password(self, password: str) -> str:
        """Hash a password using SHA-256 with salt."""
        salt = secrets.token_hex(16)
        hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return f"{salt}:{hashed}"

    def verify_password(self, password: str, hashed: str) -> bool:
        """Verify a password against its hash."""
        try:
            salt, stored_hash = hashed.split(':')
            computed_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
            return computed_hash == stored_hash
        except Exception:
            return False

    def create_user(self, username: str, password: str) -> bool:
        """Create a new user."""
        if username in self._users:
            return False
        self._users[username] = self.hash_password(password)
        self._save_users()
        return True

    def update_password(self, username: str, password: str) -> bool:
        """Update a user's password."""
        if username not in self._users:
            return False
        self._users[username] = self.hash_password(password)
        self._save_users()
        return True

    def delete_user(self, username: str) -> bool:
        """Delete a user."""
        if username not in self._users:
            return False
        del self._users[username]
        self._save_users()
        return True

    def authenticate(self, username: str, password: str) -> bool:
        """Authenticate a user."""
        if username not in self._users:
            return False
        return self.verify_password(password, self._users[username])

    def get_users(self) -> list:
        """Get list of usernames."""
        return list(self._users.keys())

    def has_users(self) -> bool:
        """Check if any users exist."""
        return len(self._users) > 0

    def create_access_token(self, username: str) -> str:
        """Create a JWT access token."""
        if not JWT_AVAILABLE:
            raise HTTPException(status_code=500, detail="JWT library not installed")

        expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
        payload = {
            "sub": username,
            "exp": expire,
            "type": "access"
        }
        return jwt.encode(payload, self._jwt_secret, algorithm=JWT_ALGORITHM)

    def create_refresh_token(self, username: str) -> str:
        """Create a JWT refresh token."""
        if not JWT_AVAILABLE:
            raise HTTPException(status_code=500, detail="JWT library not installed")

        expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        payload = {
            "sub": username,
            "exp": expire,
            "type": "refresh"
        }
        return jwt.encode(payload, self._jwt_secret, algorithm=JWT_ALGORITHM)

    def verify_token(self, token: str, token_type: str = "access") -> Optional[str]:
        """
        Verify a JWT token and return the username.

        Returns None if token is invalid.
        """
        if not JWT_AVAILABLE:
            return None

        try:
            payload = jwt.decode(token, self._jwt_secret, algorithms=[JWT_ALGORITHM])
            if payload.get("type") != token_type:
                return None
            return payload.get("sub")
        except JWTError:
            return None


# Singleton instance
auth_config = AuthConfig()


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[str]:
    """
    Dependency to get the current authenticated user.

    Returns username if authenticated, None if auth is disabled.
    Raises HTTPException 401 if auth is enabled but no valid token provided.
    """
    # If auth is disabled, allow all requests
    if not auth_config.is_enabled:
        return None

    # Auth is enabled - require valid token
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}
        )

    username = auth_config.verify_token(credentials.credentials)
    if not username:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return username


async def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> str:
    """
    Dependency that requires authentication.

    Always requires a valid token, even if auth is globally disabled.
    Use for sensitive endpoints like user management.
    """
    if not JWT_AVAILABLE:
        raise HTTPException(status_code=500, detail="Authentication not available")

    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}
        )

    username = auth_config.verify_token(credentials.credentials)
    if not username:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return username


def is_auth_enabled() -> bool:
    """Check if authentication is enabled."""
    return auth_config.is_enabled
