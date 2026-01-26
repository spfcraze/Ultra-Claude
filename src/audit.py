"""
Audit logging for UltraClaude security events.

Provides structured logging for authentication, sensitive operations,
and security-relevant events. Logs are written to a dedicated audit log file
and the standard logger.
"""
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from enum import Enum

from .logging_config import get_logger

logger = get_logger("ultraclaude.audit")

# Audit log file
DATA_DIR = Path.home() / ".ultraclaude"
AUDIT_LOG_FILE = DATA_DIR / "audit.log"
MAX_AUDIT_ENTRIES = 10000


class AuditEventType(str, Enum):
    """Types of auditable events."""
    # Authentication
    AUTH_LOGIN_SUCCESS = "auth.login.success"
    AUTH_LOGIN_FAILED = "auth.login.failed"
    AUTH_SETUP = "auth.setup"
    AUTH_PASSWORD_CHANGE = "auth.password_change"
    AUTH_ENABLED = "auth.enabled"
    AUTH_DISABLED = "auth.disabled"
    AUTH_TOKEN_REFRESH = "auth.token_refresh"

    # Session operations
    SESSION_CREATED = "session.created"
    SESSION_STOPPED = "session.stopped"
    SESSION_DELETED = "session.deleted"

    # Project operations
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_DELETED = "project.deleted"

    # Workflow operations
    WORKFLOW_CREATED = "workflow.created"
    WORKFLOW_CANCELLED = "workflow.cancelled"
    WORKFLOW_DELETED = "workflow.deleted"

    # Webhook events
    WEBHOOK_RECEIVED = "webhook.received"
    WEBHOOK_SIGNATURE_FAILED = "webhook.signature_failed"
    WEBHOOK_NO_SECRET = "webhook.no_secret"

    # Security events
    RATE_LIMIT_HIT = "security.rate_limit"
    PATH_TRAVERSAL_ATTEMPT = "security.path_traversal"
    PROTECTED_SETTING_ACCESS = "security.protected_setting"
    SQL_INJECTION_ATTEMPT = "security.sql_injection"


class AuditLogger:
    """Structured audit logger for security events."""

    _instance: Optional['AuditLogger'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._initialized = True

    def log(
        self,
        event_type: AuditEventType,
        source_ip: str = "unknown",
        username: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        success: bool = True,
    ):
        """
        Log an audit event.

        Args:
            event_type: The type of event
            source_ip: Client IP address
            username: Username if authenticated
            details: Additional event details
            success: Whether the operation succeeded
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": event_type.value,
            "source_ip": source_ip,
            "username": username,
            "success": success,
            "details": details or {},
        }

        # Log to standard logger
        level = "info" if success else "warning"
        msg = f"AUDIT: {event_type.value} | ip={source_ip} | user={username or 'anonymous'}"
        if details:
            # Don't log sensitive fields
            safe_details = {
                k: v for k, v in details.items()
                if k not in ('password', 'token', 'api_key', 'secret')
            }
            if safe_details:
                msg += f" | {safe_details}"

        if level == "info":
            logger.info(msg)
        else:
            logger.warning(msg)

        # Append to audit log file
        try:
            with open(AUDIT_LOG_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")

            # Rotate if too large
            self._rotate_if_needed()
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    def _rotate_if_needed(self):
        """Rotate audit log if it exceeds max entries."""
        try:
            if not AUDIT_LOG_FILE.exists():
                return

            line_count = sum(1 for _ in open(AUDIT_LOG_FILE))
            if line_count > MAX_AUDIT_ENTRIES:
                # Keep last half of entries
                lines = open(AUDIT_LOG_FILE).readlines()
                keep = lines[-(MAX_AUDIT_ENTRIES // 2):]
                with open(AUDIT_LOG_FILE, "w") as f:
                    f.writelines(keep)
        except Exception:
            pass

    def get_recent(self, limit: int = 100, event_type: Optional[str] = None) -> list:
        """
        Get recent audit log entries.

        Args:
            limit: Maximum entries to return
            event_type: Optional filter by event type

        Returns:
            List of audit entries (newest first)
        """
        entries = []
        try:
            if not AUDIT_LOG_FILE.exists():
                return []

            with open(AUDIT_LOG_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if event_type and entry.get("event") != event_type:
                            continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue

            # Return newest first, limited
            return list(reversed(entries[-limit:]))
        except Exception as e:
            logger.error(f"Failed to read audit log: {e}")
            return []

    def get_failed_logins(self, limit: int = 50) -> list:
        """Get recent failed login attempts."""
        return self.get_recent(
            limit=limit,
            event_type=AuditEventType.AUTH_LOGIN_FAILED.value
        )


def get_client_ip(request) -> str:
    """Extract client IP from a FastAPI request."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# Singleton instance
audit_logger = AuditLogger()
