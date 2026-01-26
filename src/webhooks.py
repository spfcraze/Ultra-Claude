"""
Webhook handling for UltraClaude automation.

Supports:
- GitHub webhooks (issue.opened, pull_request.merged, etc.)
- Custom webhooks for external triggers
"""
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Any

from .logging_config import get_logger

logger = get_logger("ultraclaude.webhooks")


class WebhookEventType(Enum):
    """Types of webhook events."""
    # GitHub events
    GITHUB_ISSUE_OPENED = "github.issue.opened"
    GITHUB_ISSUE_CLOSED = "github.issue.closed"
    GITHUB_ISSUE_LABELED = "github.issue.labeled"
    GITHUB_PR_OPENED = "github.pull_request.opened"
    GITHUB_PR_CLOSED = "github.pull_request.closed"
    GITHUB_PR_MERGED = "github.pull_request.merged"
    GITHUB_PUSH = "github.push"
    # Custom events
    CUSTOM = "custom"


@dataclass
class WebhookEvent:
    """Represents a webhook event."""
    id: str
    event_type: WebhookEventType
    source: str  # e.g., "github", "custom"
    project_id: Optional[int]
    payload: Dict[str, Any]
    headers: Dict[str, str]
    processed: bool = False
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "source": self.source,
            "project_id": self.project_id,
            "processed": self.processed,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
        }


@dataclass
class WebhookConfig:
    """Configuration for a project's webhooks."""
    project_id: int
    enabled: bool = True
    github_secret: str = ""  # Secret for verifying GitHub webhooks
    auto_queue_issues: bool = True  # Auto-queue new issues
    auto_start_on_label: str = ""  # Start session when this label is added
    trigger_labels: List[str] = field(default_factory=list)  # Labels that trigger automation
    ignore_labels: List[str] = field(default_factory=list)  # Labels to ignore

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "enabled": self.enabled,
            "has_secret": bool(self.github_secret),
            "auto_queue_issues": self.auto_queue_issues,
            "auto_start_on_label": self.auto_start_on_label,
            "trigger_labels": self.trigger_labels,
            "ignore_labels": self.ignore_labels,
        }


class WebhookHandler:
    """Handles incoming webhooks and triggers automation."""

    def __init__(self):
        self._configs: Dict[int, WebhookConfig] = {}
        self._event_log: List[WebhookEvent] = []
        self._max_log_size = 1000
        self._callbacks: Dict[str, List[Callable]] = {}

    # ==================== Configuration ====================

    def set_config(self, config: WebhookConfig):
        """Set webhook configuration for a project."""
        self._configs[config.project_id] = config

    def get_config(self, project_id: int) -> Optional[WebhookConfig]:
        """Get webhook configuration for a project."""
        return self._configs.get(project_id)

    def remove_config(self, project_id: int):
        """Remove webhook configuration for a project."""
        if project_id in self._configs:
            del self._configs[project_id]

    # ==================== GitHub Webhook Verification ====================

    def verify_github_signature(
        self,
        payload: bytes,
        signature: str,
        secret: str
    ) -> bool:
        """Verify GitHub webhook signature."""
        if not signature or not secret:
            return False

        # GitHub sends signature as "sha256=..."
        if signature.startswith("sha256="):
            expected = signature[7:]
            computed = hmac.new(
                secret.encode(),
                payload,
                hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(computed, expected)
        elif signature.startswith("sha1="):
            expected = signature[5:]
            computed = hmac.new(
                secret.encode(),
                payload,
                hashlib.sha1
            ).hexdigest()
            return hmac.compare_digest(computed, expected)

        return False

    def find_project_by_repo(self, repo_full_name: str) -> Optional[int]:
        """Find project ID by GitHub repo name."""
        from .models import project_manager

        for project in project_manager.get_all():
            if project.github_repo == repo_full_name:
                return project.id
        return None

    # ==================== GitHub Webhook Processing ====================

    async def process_github_webhook(
        self,
        event_type: str,
        payload: dict,
        headers: dict,
        raw_payload: bytes = None
    ) -> dict:
        """Process an incoming GitHub webhook."""
        import uuid

        # Determine repository
        repo = payload.get("repository", {})
        repo_name = repo.get("full_name", "")

        # Find associated project
        project_id = self.find_project_by_repo(repo_name)
        if not project_id:
            return {
                "success": False,
                "error": f"No project found for repository: {repo_name}",
            }

        # Check configuration
        config = self._configs.get(project_id)
        if not config or not config.enabled:
            return {
                "success": False,
                "error": "Webhooks not enabled for this project",
            }

        # Verify signature if secret is configured
        if config.github_secret and raw_payload:
            signature = headers.get("x-hub-signature-256", headers.get("x-hub-signature", ""))
            if not self.verify_github_signature(raw_payload, signature, config.github_secret):
                logger.warning(f"Invalid webhook signature for project {project_id}")
                return {
                    "success": False,
                    "error": "Invalid signature",
                }

        # Create event record
        event = WebhookEvent(
            id=str(uuid.uuid4()),
            event_type=self._map_github_event(event_type, payload),
            source="github",
            project_id=project_id,
            payload=payload,
            headers=dict(headers),
        )

        # Process based on event type
        try:
            result = await self._handle_github_event(event, config)
            event.processed = True
            event.result = result.get("action", "processed")
            self._log_event(event)
            return {"success": True, **result}

        except Exception as e:
            event.error = str(e)
            self._log_event(event)
            logger.error(f"Webhook processing error: {e}")
            return {"success": False, "error": str(e)}

    def _map_github_event(self, event_type: str, payload: dict) -> WebhookEventType:
        """Map GitHub event type to WebhookEventType."""
        action = payload.get("action", "")

        if event_type == "issues":
            if action == "opened":
                return WebhookEventType.GITHUB_ISSUE_OPENED
            elif action == "closed":
                return WebhookEventType.GITHUB_ISSUE_CLOSED
            elif action == "labeled":
                return WebhookEventType.GITHUB_ISSUE_LABELED
        elif event_type == "pull_request":
            if action == "opened":
                return WebhookEventType.GITHUB_PR_OPENED
            elif action == "closed":
                if payload.get("pull_request", {}).get("merged"):
                    return WebhookEventType.GITHUB_PR_MERGED
                return WebhookEventType.GITHUB_PR_CLOSED
        elif event_type == "push":
            return WebhookEventType.GITHUB_PUSH

        return WebhookEventType.CUSTOM

    async def _handle_github_event(
        self,
        event: WebhookEvent,
        config: WebhookConfig
    ) -> dict:
        """Handle a GitHub webhook event."""
        if event.event_type == WebhookEventType.GITHUB_ISSUE_OPENED:
            return await self._handle_issue_opened(event, config)
        elif event.event_type == WebhookEventType.GITHUB_ISSUE_LABELED:
            return await self._handle_issue_labeled(event, config)
        elif event.event_type == WebhookEventType.GITHUB_PR_MERGED:
            return await self._handle_pr_merged(event, config)
        elif event.event_type == WebhookEventType.GITHUB_PR_CLOSED:
            return await self._handle_pr_closed(event, config)
        else:
            return {"action": "ignored", "reason": "Unhandled event type"}

    async def _handle_issue_opened(
        self,
        event: WebhookEvent,
        config: WebhookConfig
    ) -> dict:
        """Handle new issue opened."""
        from .models import project_manager, issue_session_manager, GitHubIssue

        issue_data = event.payload.get("issue", {})
        issue_number = issue_data.get("number")
        labels = [l.get("name", "") for l in issue_data.get("labels", [])]

        # Check if we should ignore based on labels
        if config.ignore_labels:
            for label in labels:
                if label in config.ignore_labels:
                    return {"action": "ignored", "reason": f"Has ignore label: {label}"}

        # Check if issue already exists
        existing = issue_session_manager.get_by_issue(config.project_id, issue_number)
        if existing:
            return {"action": "ignored", "reason": "Issue session already exists"}

        if not config.auto_queue_issues:
            return {"action": "ignored", "reason": "Auto-queue disabled"}

        # Create GitHub issue object
        issue = GitHubIssue(
            number=issue_number,
            title=issue_data.get("title", ""),
            body=issue_data.get("body", "") or "",
            labels=labels,
            assignees=[a.get("login", "") for a in issue_data.get("assignees", [])],
            state=issue_data.get("state", "open"),
            html_url=issue_data.get("html_url", ""),
            created_at=issue_data.get("created_at", ""),
            updated_at=issue_data.get("updated_at", ""),
        )

        # Create issue session
        session = issue_session_manager.create(config.project_id, issue)
        logger.info(f"Auto-queued issue #{issue_number}: {issue.title}")

        # Emit event
        await self._emit_event("issue_queued", {
            "project_id": config.project_id,
            "issue_number": issue_number,
            "issue_title": issue.title,
            "session_id": session.id,
        })

        # Check if we should auto-start
        should_start = False
        if config.trigger_labels:
            for label in labels:
                if label in config.trigger_labels:
                    should_start = True
                    break

        if should_start:
            from .automation import automation_controller
            await automation_controller.start_issue_session(session)
            return {
                "action": "started",
                "issue_number": issue_number,
                "session_id": session.id,
            }

        return {
            "action": "queued",
            "issue_number": issue_number,
            "session_id": session.id,
        }

    async def _handle_issue_labeled(
        self,
        event: WebhookEvent,
        config: WebhookConfig
    ) -> dict:
        """Handle issue labeled event."""
        from .models import issue_session_manager, IssueSessionStatus

        issue_data = event.payload.get("issue", {})
        issue_number = issue_data.get("number")
        label = event.payload.get("label", {}).get("name", "")

        # Check if we should start on this label
        if config.auto_start_on_label and label == config.auto_start_on_label:
            session = issue_session_manager.get_by_issue(config.project_id, issue_number)
            if session and session.status == IssueSessionStatus.PENDING:
                from .automation import automation_controller
                await automation_controller.start_issue_session(session)
                return {
                    "action": "started",
                    "issue_number": issue_number,
                    "trigger": f"label:{label}",
                }

        return {"action": "ignored", "reason": "No action for this label"}

    async def _handle_pr_merged(
        self,
        event: WebhookEvent,
        config: WebhookConfig
    ) -> dict:
        """Handle PR merged event."""
        from .models import issue_session_manager, IssueSessionStatus

        pr_data = event.payload.get("pull_request", {})
        pr_number = pr_data.get("number")

        # Find session with this PR
        for session in issue_session_manager.sessions.values():
            if session.project_id == config.project_id and session.pr_number == pr_number:
                issue_session_manager.update(
                    session.id,
                    status=IssueSessionStatus.COMPLETED,
                    completed_at=datetime.now().isoformat()
                )
                logger.info(f"PR #{pr_number} merged, issue session marked complete")

                await self._emit_event("pr_merged", {
                    "project_id": config.project_id,
                    "pr_number": pr_number,
                    "issue_number": session.github_issue_number,
                })

                return {
                    "action": "completed",
                    "pr_number": pr_number,
                    "issue_number": session.github_issue_number,
                }

        return {"action": "ignored", "reason": "No matching session found"}

    async def _handle_pr_closed(
        self,
        event: WebhookEvent,
        config: WebhookConfig
    ) -> dict:
        """Handle PR closed without merge event."""
        from .models import issue_session_manager, IssueSessionStatus

        pr_data = event.payload.get("pull_request", {})
        pr_number = pr_data.get("number")

        # Find session with this PR
        for session in issue_session_manager.sessions.values():
            if session.project_id == config.project_id and session.pr_number == pr_number:
                issue_session_manager.update(
                    session.id,
                    status=IssueSessionStatus.FAILED,
                    last_error="PR closed without merge"
                )
                logger.info(f"PR #{pr_number} closed, issue session marked failed")

                return {
                    "action": "failed",
                    "pr_number": pr_number,
                    "reason": "PR closed without merge",
                }

        return {"action": "ignored", "reason": "No matching session found"}

    # ==================== Custom Webhooks ====================

    async def process_custom_webhook(
        self,
        path: str,
        payload: dict,
        headers: dict
    ) -> dict:
        """Process a custom webhook."""
        import uuid

        event = WebhookEvent(
            id=str(uuid.uuid4()),
            event_type=WebhookEventType.CUSTOM,
            source=f"custom:{path}",
            project_id=None,
            payload=payload,
            headers=dict(headers),
        )

        # Emit event for handlers
        await self._emit_event(f"webhook:{path}", {
            "path": path,
            "payload": payload,
        })

        event.processed = True
        self._log_event(event)

        return {"success": True, "action": "processed"}

    # ==================== Event Logging ====================

    def _log_event(self, event: WebhookEvent):
        """Log a webhook event."""
        self._event_log.append(event)
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size:]

    def get_event_log(self, limit: int = 100) -> List[dict]:
        """Get recent webhook events."""
        return [e.to_dict() for e in self._event_log[-limit:]]

    def get_events_by_project(self, project_id: int, limit: int = 50) -> List[dict]:
        """Get webhook events for a specific project."""
        events = [e for e in self._event_log if e.project_id == project_id]
        return [e.to_dict() for e in events[-limit:]]

    # ==================== Events ====================

    def add_event_callback(self, event: str, callback: Callable):
        """Add callback for webhook events."""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    async def _emit_event(self, event: str, data: dict):
        """Emit an event to all registered callbacks."""
        import asyncio

        if event in self._callbacks:
            for callback in self._callbacks[event]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Event callback error: {e}")

    # ==================== Status ====================

    def get_status(self) -> dict:
        """Get webhook handler status."""
        return {
            "enabled_projects": len([c for c in self._configs.values() if c.enabled]),
            "total_events": len(self._event_log),
            "recent_events": self.get_event_log(10),
        }


# Global instance
webhook_handler = WebhookHandler()
