"""
Multi-channel notification system for UltraClaude.

Supports:
- Discord webhooks
- Slack webhooks
- Email notifications (SMTP)
- Desktop notifications (existing)
"""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import httpx

from .logging_config import get_logger

logger = get_logger("ultraclaude.notifications")


class NotificationChannel(Enum):
    """Available notification channels."""
    DISCORD = "discord"
    SLACK = "slack"
    TELEGRAM = "telegram"
    EMAIL = "email"
    DESKTOP = "desktop"


class NotificationEvent(Enum):
    """Events that can trigger notifications."""
    # Issue events
    ISSUE_STARTED = "issue.started"
    ISSUE_COMPLETED = "issue.completed"
    ISSUE_FAILED = "issue.failed"
    ISSUE_NEEDS_REVIEW = "issue.needs_review"
    ISSUE_QUEUED = "issue.queued"
    # PR events
    PR_CREATED = "pr.created"
    PR_MERGED = "pr.merged"
    # Session events
    SESSION_ERROR = "session.error"
    SESSION_NEEDS_ATTENTION = "session.needs_attention"
    # System events
    SYSTEM_UPDATE_AVAILABLE = "system.update_available"
    SYSTEM_HEALTH_WARNING = "system.health_warning"


@dataclass
class NotificationConfig:
    """Configuration for notification channels."""
    enabled: bool = True
    events: List[str] = field(default_factory=list)  # Events to notify on

    # Discord
    discord_enabled: bool = False
    discord_webhook_url: str = ""

    # Slack
    slack_enabled: bool = False
    slack_webhook_url: str = ""

    # Telegram
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Email
    email_enabled: bool = False
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_user: str = ""
    email_smtp_password: str = ""
    email_from: str = ""
    email_to: List[str] = field(default_factory=list)
    email_use_tls: bool = True

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "events": self.events,
            "discord_enabled": self.discord_enabled,
            "discord_webhook_configured": bool(self.discord_webhook_url),
            "slack_enabled": self.slack_enabled,
            "slack_webhook_configured": bool(self.slack_webhook_url),
            "telegram_enabled": self.telegram_enabled,
            "telegram_configured": bool(self.telegram_bot_token and self.telegram_chat_id),
            "email_enabled": self.email_enabled,
            "email_configured": bool(self.email_smtp_host and self.email_to),
        }


@dataclass
class Notification:
    """Represents a notification to be sent."""
    event: NotificationEvent
    title: str
    message: str
    project_id: Optional[int] = None
    project_name: Optional[str] = None
    issue_number: Optional[int] = None
    pr_number: Optional[int] = None
    url: Optional[str] = None
    severity: str = "info"  # info, warning, error, success
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "event": self.event.value,
            "title": self.title,
            "message": self.message,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "issue_number": self.issue_number,
            "pr_number": self.pr_number,
            "url": self.url,
            "severity": self.severity,
            "created_at": self.created_at,
        }


@dataclass
class ChannelConfig:
    """Individual notification channel configuration for API management."""
    id: str
    name: str
    channel: NotificationChannel
    enabled: bool = True
    project_id: Optional[int] = None
    events: List[str] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "channel": self.channel.value,
            "enabled": self.enabled,
            "project_id": self.project_id,
            "events": self.events,
            "settings": {
                k: ("***" if k in ("bot_token",) else v)
                for k, v in self.settings.items()
                if not k.endswith("_password")  # Don't expose passwords
            },
            "created_at": self.created_at,
        }


class NotificationManager:
    """Manages multi-channel notifications for UltraClaude."""

    def __init__(self):
        self._global_config = NotificationConfig()
        self._project_configs: Dict[int, NotificationConfig] = {}
        self._notification_log: List[dict] = []
        self._max_log_size = 500
        self._callbacks: List[Callable] = []
        # Channel-based configs for API management
        self._channel_configs: Dict[str, ChannelConfig] = {}

    # ==================== Channel Config Management (API) ====================

    def get_all_configs(self) -> List[ChannelConfig]:
        """Get all channel configurations."""
        return list(self._channel_configs.values())

    def get_config(self, config_id: str) -> Optional[ChannelConfig]:
        """Get a specific channel configuration."""
        return self._channel_configs.get(config_id)

    def add_config(self, config: ChannelConfig):
        """Add a new channel configuration."""
        self._channel_configs[config.id] = config
        # Also update the legacy config system
        self._sync_to_legacy_config(config)

    def remove_config(self, config_id: str) -> bool:
        """Remove a channel configuration."""
        if config_id in self._channel_configs:
            del self._channel_configs[config_id]
            return True
        return False

    def _sync_to_legacy_config(self, config: ChannelConfig):
        """Sync channel config to legacy notification config."""
        target = self._global_config
        if config.project_id:
            if config.project_id not in self._project_configs:
                self._project_configs[config.project_id] = NotificationConfig()
            target = self._project_configs[config.project_id]

        settings = config.settings
        if config.channel == NotificationChannel.DISCORD:
            target.discord_enabled = config.enabled
            target.discord_webhook_url = settings.get("webhook_url", "")
        elif config.channel == NotificationChannel.SLACK:
            target.slack_enabled = config.enabled
            target.slack_webhook_url = settings.get("webhook_url", "")
        elif config.channel == NotificationChannel.TELEGRAM:
            target.telegram_enabled = config.enabled
            target.telegram_bot_token = settings.get("bot_token", "")
            target.telegram_chat_id = settings.get("chat_id", "")
        elif config.channel == NotificationChannel.EMAIL:
            target.email_enabled = config.enabled
            target.email_smtp_host = settings.get("smtp_host", "")
            target.email_smtp_port = settings.get("smtp_port", 587)
            target.email_smtp_user = settings.get("smtp_user", "")
            target.email_smtp_password = settings.get("smtp_password", "")
            target.email_from = settings.get("smtp_from", "")
            target.email_to = settings.get("smtp_to", [])

        if config.events:
            target.events = list(set(target.events + config.events))

    async def send_test(self, config: ChannelConfig) -> bool:
        """Send a test notification through a specific channel config."""
        test_notification = Notification(
            event=NotificationEvent.SYSTEM_HEALTH_WARNING,
            title="Test Notification",
            message="This is a test notification from UltraClaude.",
            severity="info",
        )

        try:
            if config.channel == NotificationChannel.DISCORD:
                result = await self._send_discord(
                    config.settings.get("webhook_url", ""),
                    test_notification
                )
            elif config.channel == NotificationChannel.SLACK:
                result = await self._send_slack(
                    config.settings.get("webhook_url", ""),
                    test_notification
                )
            elif config.channel == NotificationChannel.TELEGRAM:
                result = await self._send_telegram(
                    config.settings.get("bot_token", ""),
                    config.settings.get("chat_id", ""),
                    test_notification
                )
            elif config.channel == NotificationChannel.EMAIL:
                # Create temp config for email
                temp = NotificationConfig(
                    email_enabled=True,
                    email_smtp_host=config.settings.get("smtp_host", ""),
                    email_smtp_port=config.settings.get("smtp_port", 587),
                    email_smtp_user=config.settings.get("smtp_user", ""),
                    email_smtp_password=config.settings.get("smtp_password", ""),
                    email_from=config.settings.get("smtp_from", ""),
                    email_to=config.settings.get("smtp_to", []),
                )
                result = await self._send_email(temp, test_notification)
            elif config.channel == NotificationChannel.DESKTOP:
                self._send_desktop(test_notification)
                result = {"success": True}
            else:
                result = {"success": False, "error": "Unknown channel"}

            return result.get("success", False)
        except Exception as e:
            logger.error(f"Test notification failed: {e}")
            return False

    # ==================== Configuration ====================

    def set_global_config(self, config: NotificationConfig):
        """Set global notification configuration."""
        self._global_config = config

    def get_global_config(self) -> NotificationConfig:
        """Get global notification configuration."""
        return self._global_config

    def set_project_config(self, project_id: int, config: NotificationConfig):
        """Set project-specific notification configuration."""
        self._project_configs[project_id] = config

    def get_project_config(self, project_id: int) -> Optional[NotificationConfig]:
        """Get project-specific notification configuration."""
        return self._project_configs.get(project_id)

    def get_effective_config(self, project_id: Optional[int]) -> NotificationConfig:
        """Get effective configuration (project or global)."""
        if project_id and project_id in self._project_configs:
            return self._project_configs[project_id]
        return self._global_config

    # ==================== Sending Notifications ====================

    async def notify(self, notification: Notification) -> dict:
        """Send notification to all configured channels."""
        config = self.get_effective_config(notification.project_id)

        if not config.enabled:
            return {"sent": False, "reason": "Notifications disabled"}

        # Check if event is in enabled events (empty = all enabled)
        if config.events and notification.event.value not in config.events:
            return {"sent": False, "reason": "Event not enabled"}

        results = {
            "sent": True,
            "channels": {},
        }

        # Send to each enabled channel
        if config.discord_enabled and config.discord_webhook_url:
            results["channels"]["discord"] = await self._send_discord(
                config.discord_webhook_url, notification
            )

        if config.slack_enabled and config.slack_webhook_url:
            results["channels"]["slack"] = await self._send_slack(
                config.slack_webhook_url, notification
            )

        if config.telegram_enabled and config.telegram_bot_token and config.telegram_chat_id:
            results["channels"]["telegram"] = await self._send_telegram(
                config.telegram_bot_token, config.telegram_chat_id, notification
            )

        if config.email_enabled and config.email_smtp_host and config.email_to:
            results["channels"]["email"] = await self._send_email(config, notification)

        # Also trigger desktop notification
        self._send_desktop(notification)

        # Log notification
        self._log_notification(notification, results)

        # Trigger callbacks
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(notification)
                else:
                    callback(notification)
            except Exception as e:
                logger.error(f"Notification callback error: {e}")

        return results

    # ==================== Discord ====================

    async def _send_discord(self, webhook_url: str, notification: Notification) -> dict:
        """Send notification via Discord webhook."""
        try:
            # Discord embed colors
            colors = {
                "info": 0x3498db,     # Blue
                "success": 0x2ecc71,  # Green
                "warning": 0xf1c40f,  # Yellow
                "error": 0xe74c3c,    # Red
            }

            # Build embed
            embed = {
                "title": notification.title,
                "description": notification.message,
                "color": colors.get(notification.severity, colors["info"]),
                "timestamp": notification.created_at,
                "footer": {
                    "text": "UltraClaude",
                },
                "fields": [],
            }

            if notification.project_name:
                embed["fields"].append({
                    "name": "Project",
                    "value": notification.project_name,
                    "inline": True,
                })

            if notification.issue_number:
                embed["fields"].append({
                    "name": "Issue",
                    "value": f"#{notification.issue_number}",
                    "inline": True,
                })

            if notification.pr_number:
                embed["fields"].append({
                    "name": "PR",
                    "value": f"#{notification.pr_number}",
                    "inline": True,
                })

            if notification.url:
                embed["url"] = notification.url

            payload = {"embeds": [embed]}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )

                if response.status_code == 204:
                    return {"success": True}
                else:
                    return {"success": False, "error": f"HTTP {response.status_code}"}

        except Exception as e:
            logger.error(f"Discord notification failed: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Slack ====================

    async def _send_slack(self, webhook_url: str, notification: Notification) -> dict:
        """Send notification via Slack webhook."""
        try:
            # Slack message colors
            colors = {
                "info": "#3498db",
                "success": "#2ecc71",
                "warning": "#f1c40f",
                "error": "#e74c3c",
            }

            # Build attachment
            attachment = {
                "color": colors.get(notification.severity, colors["info"]),
                "title": notification.title,
                "text": notification.message,
                "footer": "UltraClaude",
                "ts": int(datetime.fromisoformat(notification.created_at.replace("Z", "+00:00")).timestamp()),
                "fields": [],
            }

            if notification.project_name:
                attachment["fields"].append({
                    "title": "Project",
                    "value": notification.project_name,
                    "short": True,
                })

            if notification.issue_number:
                attachment["fields"].append({
                    "title": "Issue",
                    "value": f"#{notification.issue_number}",
                    "short": True,
                })

            if notification.pr_number:
                attachment["fields"].append({
                    "title": "PR",
                    "value": f"#{notification.pr_number}",
                    "short": True,
                })

            if notification.url:
                attachment["title_link"] = notification.url

            payload = {"attachments": [attachment]}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )

                if response.status_code == 200 and response.text == "ok":
                    return {"success": True}
                else:
                    return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}

        except Exception as e:
            logger.error(f"Slack notification failed: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Telegram ====================

    async def _send_telegram(self, bot_token: str, chat_id: str, notification: Notification) -> dict:
        """Send notification via Telegram Bot API."""
        try:
            # Severity indicators
            indicators = {
                "info": "\u2139\ufe0f",
                "success": "\u2705",
                "warning": "\u26a0\ufe0f",
                "error": "\u274c",
            }
            indicator = indicators.get(notification.severity, indicators["info"])

            # Build message with HTML formatting
            lines = [
                f"{indicator} <b>{self._escape_html(notification.title)}</b>",
                "",
                self._escape_html(notification.message),
            ]

            if notification.project_name:
                lines.append(f"\n<b>Project:</b> {self._escape_html(notification.project_name)}")
            if notification.issue_number:
                lines.append(f"<b>Issue:</b> #{notification.issue_number}")
            if notification.pr_number:
                lines.append(f"<b>PR:</b> #{notification.pr_number}")
            if notification.url:
                lines.append(f"\n<a href=\"{notification.url}\">View on GitHub</a>")

            lines.append(f"\n<i>UltraClaude</i>")

            text = "\n".join(lines)

            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                result = response.json()

                if result.get("ok"):
                    return {"success": True}
                else:
                    error_desc = result.get("description", f"HTTP {response.status_code}")
                    return {"success": False, "error": error_desc}

        except Exception as e:
            logger.error(f"Telegram notification failed: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters for Telegram HTML parse mode."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    # ==================== Email ====================

    async def _send_email(self, config: NotificationConfig, notification: Notification) -> dict:
        """Send notification via email."""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            # Build email
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[UltraClaude] {notification.title}"
            msg["From"] = config.email_from or config.email_smtp_user
            msg["To"] = ", ".join(config.email_to)

            # Plain text version
            text_content = f"""
{notification.title}

{notification.message}

"""
            if notification.project_name:
                text_content += f"Project: {notification.project_name}\n"
            if notification.issue_number:
                text_content += f"Issue: #{notification.issue_number}\n"
            if notification.pr_number:
                text_content += f"PR: #{notification.pr_number}\n"
            if notification.url:
                text_content += f"\nLink: {notification.url}\n"

            text_content += "\n--\nSent by UltraClaude"

            # HTML version
            severity_colors = {
                "info": "#3498db",
                "success": "#2ecc71",
                "warning": "#f1c40f",
                "error": "#e74c3c",
            }
            color = severity_colors.get(notification.severity, severity_colors["info"])

            html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; }}
        .container {{ max-width: 600px; margin: 0 auto; }}
        .header {{ background: {color}; color: white; padding: 15px; border-radius: 5px 5px 0 0; }}
        .content {{ background: #f9f9f9; padding: 20px; border: 1px solid #ddd; border-top: none; }}
        .meta {{ color: #666; font-size: 14px; margin-top: 15px; }}
        .footer {{ color: #999; font-size: 12px; margin-top: 20px; padding-top: 10px; border-top: 1px solid #eee; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2 style="margin: 0;">{notification.title}</h2>
        </div>
        <div class="content">
            <p>{notification.message}</p>
            <div class="meta">
                {f'<p><strong>Project:</strong> {notification.project_name}</p>' if notification.project_name else ''}
                {f'<p><strong>Issue:</strong> #{notification.issue_number}</p>' if notification.issue_number else ''}
                {f'<p><strong>PR:</strong> #{notification.pr_number}</p>' if notification.pr_number else ''}
                {f'<p><a href="{notification.url}">View on GitHub</a></p>' if notification.url else ''}
            </div>
            <div class="footer">
                Sent by UltraClaude
            </div>
        </div>
    </div>
</body>
</html>
"""

            msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            # Send email
            def send():
                if config.email_use_tls:
                    with smtplib.SMTP(config.email_smtp_host, config.email_smtp_port) as server:
                        server.starttls()
                        if config.email_smtp_user and config.email_smtp_password:
                            server.login(config.email_smtp_user, config.email_smtp_password)
                        server.send_message(msg)
                else:
                    with smtplib.SMTP(config.email_smtp_host, config.email_smtp_port) as server:
                        if config.email_smtp_user and config.email_smtp_password:
                            server.login(config.email_smtp_user, config.email_smtp_password)
                        server.send_message(msg)

            # Run in executor to not block
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, send)

            return {"success": True}

        except Exception as e:
            logger.error(f"Email notification failed: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Desktop ====================

    def _send_desktop(self, notification: Notification):
        """Send desktop notification (existing functionality)."""
        try:
            from .notifier import notify
            notify(
                title=notification.title,
                message=notification.message,
            )
        except Exception as e:
            logger.debug(f"Desktop notification not available: {e}")

    # ==================== Helper Methods ====================

    async def notify_issue_started(
        self,
        project_id: int,
        project_name: str,
        issue_number: int,
        issue_title: str,
        issue_url: str = ""
    ):
        """Send notification when issue work starts."""
        await self.notify(Notification(
            event=NotificationEvent.ISSUE_STARTED,
            title=f"Started: #{issue_number}",
            message=f"Claude started working on: {issue_title}",
            project_id=project_id,
            project_name=project_name,
            issue_number=issue_number,
            url=issue_url,
            severity="info",
        ))

    async def notify_issue_completed(
        self,
        project_id: int,
        project_name: str,
        issue_number: int,
        issue_title: str,
        pr_number: Optional[int] = None,
        pr_url: str = ""
    ):
        """Send notification when issue is completed."""
        message = f"Completed: {issue_title}"
        if pr_number:
            message += f"\nPR #{pr_number} created"

        await self.notify(Notification(
            event=NotificationEvent.ISSUE_COMPLETED,
            title=f"Completed: #{issue_number}",
            message=message,
            project_id=project_id,
            project_name=project_name,
            issue_number=issue_number,
            pr_number=pr_number,
            url=pr_url,
            severity="success",
        ))

    async def notify_issue_failed(
        self,
        project_id: int,
        project_name: str,
        issue_number: int,
        issue_title: str,
        error: str,
        attempt: int,
        max_attempts: int,
        issue_url: str = ""
    ):
        """Send notification when issue work fails."""
        await self.notify(Notification(
            event=NotificationEvent.ISSUE_FAILED,
            title=f"Failed: #{issue_number}",
            message=f"Failed to complete: {issue_title}\n\nError: {error}\n\nAttempt {attempt}/{max_attempts}",
            project_id=project_id,
            project_name=project_name,
            issue_number=issue_number,
            url=issue_url,
            severity="error",
        ))

    async def notify_needs_review(
        self,
        project_id: int,
        project_name: str,
        issue_number: int,
        issue_title: str,
        reason: str,
        issue_url: str = ""
    ):
        """Send notification when issue needs human review."""
        await self.notify(Notification(
            event=NotificationEvent.ISSUE_NEEDS_REVIEW,
            title=f"Needs Review: #{issue_number}",
            message=f"Issue flagged for human review: {issue_title}\n\nReason: {reason}",
            project_id=project_id,
            project_name=project_name,
            issue_number=issue_number,
            url=issue_url,
            severity="warning",
        ))

    async def notify_pr_created(
        self,
        project_id: int,
        project_name: str,
        issue_number: int,
        pr_number: int,
        pr_title: str,
        pr_url: str
    ):
        """Send notification when PR is created."""
        await self.notify(Notification(
            event=NotificationEvent.PR_CREATED,
            title=f"PR Created: #{pr_number}",
            message=f"{pr_title}\n\nFor issue #{issue_number}",
            project_id=project_id,
            project_name=project_name,
            issue_number=issue_number,
            pr_number=pr_number,
            url=pr_url,
            severity="success",
        ))

    async def notify_session_error(
        self,
        session_name: str,
        session_id: int,
        error: str,
        project_id: Optional[int] = None,
        project_name: Optional[str] = None
    ):
        """Send notification when session encounters an error."""
        await self.notify(Notification(
            event=NotificationEvent.SESSION_ERROR,
            title=f"Session Error: {session_name}",
            message=f"Session #{session_id} encountered an error:\n\n{error}",
            project_id=project_id,
            project_name=project_name,
            severity="error",
        ))

    async def notify_update_available(self, current_version: str, latest_version: str):
        """Send notification when update is available."""
        await self.notify(Notification(
            event=NotificationEvent.SYSTEM_UPDATE_AVAILABLE,
            title="Update Available",
            message=f"UltraClaude {latest_version} is available (you have {current_version})",
            url="https://github.com/spfcraze/Ultra-Claude/releases",
            severity="info",
        ))

    # ==================== Logging ====================

    def _log_notification(self, notification: Notification, result: dict):
        """Log a sent notification."""
        log_entry = {
            **notification.to_dict(),
            "result": result,
        }
        self._notification_log.append(log_entry)
        if len(self._notification_log) > self._max_log_size:
            self._notification_log = self._notification_log[-self._max_log_size:]

    def get_notification_log(self, limit: int = 100) -> List[dict]:
        """Get recent notification log."""
        return self._notification_log[-limit:]

    def add_callback(self, callback: Callable):
        """Add callback for all notifications."""
        self._callbacks.append(callback)

    def get_status(self) -> dict:
        """Get notification system status."""
        return {
            "global_enabled": self._global_config.enabled,
            "global_config": self._global_config.to_dict(),
            "project_count": len(self._project_configs),
            "total_sent": len(self._notification_log),
        }


# Global instance
notification_manager = NotificationManager()


# Keep existing desktop notification function for backwards compatibility
def notify_session_needs_attention(session_name: str, session_id: int):
    """Desktop notification for session needing attention (backwards compatible)."""
    try:
        from .notifier import notify
        notify(
            title="Session Needs Attention",
            message=f"Session '{session_name}' (#{session_id}) needs your attention",
        )
    except Exception as e:
        logger.debug(f"Desktop notification not available: {e}")
