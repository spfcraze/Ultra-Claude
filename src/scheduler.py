"""
Task scheduler for Autowrkers automation.

Provides cron-like scheduling for:
- Automatic issue syncing
- Session health checks
- Cleanup of old sessions
- Auto-retry of failed issues
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore

from .logging_config import get_logger

logger = get_logger("autowrkers.scheduler")


class TaskType(Enum):
    """Types of scheduled tasks."""
    ISSUE_SYNC = "issue_sync"
    HEALTH_CHECK = "health_check"
    SESSION_CLEANUP = "session_cleanup"
    AUTO_RETRY = "auto_retry"
    PR_STATUS_CHECK = "pr_status_check"
    CUSTOM = "custom"


class TaskStatus(Enum):
    """Status of a scheduled task."""
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


@dataclass
class ScheduledTask:
    """Represents a scheduled task configuration."""
    id: str
    name: str
    task_type: TaskType
    schedule: str  # Cron expression or interval (e.g., "*/15 * * * *" or "15m")
    enabled: bool = True
    project_id: Optional[int] = None
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    run_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type.value,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "project_id": self.project_id,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "config": self.config,
        }


class TaskScheduler:
    """Manages scheduled tasks for Autowrkers automation."""

    def __init__(self):
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            job_defaults={
                "coalesce": True,  # Combine missed runs
                "max_instances": 1,  # Prevent overlapping runs
            }
        )
        self._tasks: Dict[str, ScheduledTask] = {}
        self._callbacks: Dict[str, List[Callable]] = {}
        self._started = False

    # ==================== Lifecycle ====================

    def start(self):
        """Start the scheduler."""
        if not self._started:
            self._scheduler.start()
            self._started = True
            logger.info("Task scheduler started")

    def stop(self):
        """Stop the scheduler."""
        if self._started:
            self._scheduler.shutdown()
            self._started = False
            logger.info("Task scheduler stopped")

    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._started

    # ==================== Task Management ====================

    def add_task(self, task: ScheduledTask) -> bool:
        """Add a scheduled task."""
        if task.id in self._tasks:
            logger.warning(f"Task {task.id} already exists, updating...")
            self.remove_task(task.id)

        self._tasks[task.id] = task

        if task.enabled:
            self._schedule_task(task)

        logger.info(f"Added task: {task.name} ({task.id})")
        return True

    def remove_task(self, task_id: str) -> bool:
        """Remove a scheduled task."""
        if task_id not in self._tasks:
            return False

        try:
            self._scheduler.remove_job(task_id)
        except Exception:
            pass  # Job might not be scheduled

        del self._tasks[task_id]
        logger.info(f"Removed task: {task_id}")
        return True

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> List[ScheduledTask]:
        """Get all scheduled tasks."""
        return list(self._tasks.values())

    def get_tasks_by_project(self, project_id: int) -> List[ScheduledTask]:
        """Get tasks for a specific project."""
        return [t for t in self._tasks.values() if t.project_id == project_id]

    def enable_task(self, task_id: str) -> bool:
        """Enable a task."""
        task = self._tasks.get(task_id)
        if not task:
            return False

        task.enabled = True
        self._schedule_task(task)
        return True

    def disable_task(self, task_id: str) -> bool:
        """Disable a task."""
        task = self._tasks.get(task_id)
        if not task:
            return False

        task.enabled = False
        try:
            self._scheduler.remove_job(task_id)
        except Exception:
            pass
        return True

    def run_task_now(self, task_id: str) -> bool:
        """Run a task immediately."""
        task = self._tasks.get(task_id)
        if not task:
            return False

        asyncio.create_task(self._execute_task(task))
        return True

    # ==================== Scheduling ====================

    def _parse_schedule(self, schedule: str):
        """Parse schedule string into APScheduler trigger."""
        schedule = schedule.strip()

        # Check for interval format (e.g., "15m", "1h", "30s")
        if schedule.endswith(("s", "m", "h", "d")):
            unit = schedule[-1]
            try:
                value = int(schedule[:-1])
            except ValueError:
                raise ValueError(f"Invalid interval: {schedule}")

            if unit == "s":
                return IntervalTrigger(seconds=value)
            elif unit == "m":
                return IntervalTrigger(minutes=value)
            elif unit == "h":
                return IntervalTrigger(hours=value)
            elif unit == "d":
                return IntervalTrigger(days=value)

        # Assume cron expression
        return CronTrigger.from_crontab(schedule)

    def _schedule_task(self, task: ScheduledTask):
        """Schedule a task with APScheduler."""
        try:
            trigger = self._parse_schedule(task.schedule)

            self._scheduler.add_job(
                self._execute_task,
                trigger=trigger,
                id=task.id,
                args=[task],
                replace_existing=True,
            )

            # Update next run time (may not be available until scheduler starts)
            job = self._scheduler.get_job(task.id)
            if job:
                try:
                    if hasattr(job, 'next_run_time') and job.next_run_time:
                        task.next_run = job.next_run_time.isoformat()
                except Exception:
                    pass  # next_run_time not available yet

            logger.info(f"Scheduled task: {task.name} ({task.schedule})")
        except Exception as e:
            logger.error(f"Failed to schedule task {task.id}: {e}")

    async def _execute_task(self, task: ScheduledTask):
        """Execute a scheduled task."""
        logger.info(f"Executing task: {task.name}")
        task.last_run = datetime.now().isoformat()
        task.run_count += 1

        try:
            if task.task_type == TaskType.ISSUE_SYNC:
                await self._run_issue_sync(task)
            elif task.task_type == TaskType.HEALTH_CHECK:
                await self._run_health_check(task)
            elif task.task_type == TaskType.SESSION_CLEANUP:
                await self._run_session_cleanup(task)
            elif task.task_type == TaskType.AUTO_RETRY:
                await self._run_auto_retry(task)
            elif task.task_type == TaskType.PR_STATUS_CHECK:
                await self._run_pr_status_check(task)
            elif task.task_type == TaskType.CUSTOM:
                await self._run_custom_task(task)

            task.last_error = None
            logger.info(f"Task completed: {task.name}")

        except Exception as e:
            task.error_count += 1
            task.last_error = str(e)
            logger.error(f"Task failed: {task.name} - {e}")

        # Update next run time
        job = self._scheduler.get_job(task.id)
        if job:
            try:
                if hasattr(job, 'next_run_time') and job.next_run_time:
                    task.next_run = job.next_run_time.isoformat()
            except Exception:
                pass

    # ==================== Task Implementations ====================

    async def _run_issue_sync(self, task: ScheduledTask):
        """Sync issues from GitHub for a project."""
        from .models import project_manager, issue_session_manager
        from .github_client import get_github_client

        project_id = task.project_id
        if not project_id:
            # Sync all projects with auto_sync enabled
            projects = [p for p in project_manager.get_all() if p.auto_sync]
        else:
            project = project_manager.get(project_id)
            projects = [project] if project else []

        for project in projects:
            if not project.github_token_encrypted:
                continue

            try:
                client = get_github_client(project.get_token())
                issues = await client.get_all_issues(
                    project.github_repo,
                    project.issue_filter
                )

                created_count = 0
                for issue in issues:
                    existing = issue_session_manager.get_by_issue(project.id, issue.number)
                    if not existing:
                        issue_session_manager.create(project.id, issue)
                        created_count += 1

                # Update last sync time
                project_manager.update(project.id, last_sync=datetime.now().isoformat())

                logger.info(f"Synced {len(issues)} issues for {project.name}, {created_count} new")

                # Emit event
                await self._emit_event("issue_sync", {
                    "project_id": project.id,
                    "project_name": project.name,
                    "total_issues": len(issues),
                    "new_issues": created_count,
                })

            except Exception as e:
                logger.error(f"Issue sync failed for {project.name}: {e}")

    async def _run_health_check(self, task: ScheduledTask):
        """Check health of running sessions."""
        from .session_manager import manager, SessionStatus

        sessions = manager.get_all_sessions()
        issues_found = []

        for session in sessions:
            if session.status == SessionStatus.RUNNING:
                # Check if tmux session still exists
                import subprocess
                result = subprocess.run(
                    ["tmux", "has-session", "-t", session.tmux_session],
                    capture_output=True
                )
                if result.returncode != 0:
                    # Session lost
                    session.status = SessionStatus.STOPPED
                    issues_found.append({
                        "session_id": session.id,
                        "issue": "tmux_session_lost",
                    })
                    logger.warning(f"Session {session.name} lost its tmux session")

        if issues_found:
            await self._emit_event("health_check", {
                "issues_found": len(issues_found),
                "issues": issues_found,
            })

    async def _run_session_cleanup(self, task: ScheduledTask):
        """Clean up old completed sessions."""
        from .session_manager import manager, SessionStatus
        from datetime import datetime, timedelta

        max_age_days = task.config.get("max_age_days", 7)
        cutoff = datetime.now() - timedelta(days=max_age_days)

        sessions = manager.get_all_sessions()
        removed_count = 0

        for session in sessions:
            if session.status == SessionStatus.COMPLETED:
                # Parse session creation time
                try:
                    created = datetime.fromisoformat(session.created_at.replace("Z", "+00:00"))
                    if created < cutoff:
                        await manager.remove_session(session.id)
                        removed_count += 1
                except Exception:
                    pass

        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old sessions")
            await self._emit_event("session_cleanup", {
                "removed_count": removed_count,
            })

    async def _run_auto_retry(self, task: ScheduledTask):
        """Auto-retry failed issue sessions."""
        from .models import issue_session_manager, IssueSessionStatus
        from .automation import automation_controller

        # Get failed sessions that haven't exceeded max attempts
        all_sessions = list(issue_session_manager.sessions.values())
        retriable = [
            s for s in all_sessions
            if s.status == IssueSessionStatus.FAILED and s.attempts < s.max_attempts
        ]

        retried_count = 0
        for session in retriable:
            try:
                # Reset and restart
                issue_session_manager.update(
                    session.id,
                    status=IssueSessionStatus.PENDING,
                    last_error=""
                )
                await automation_controller.start_issue_session(session)
                retried_count += 1
                logger.info(f"Retrying issue #{session.github_issue_number}")
            except Exception as e:
                logger.error(f"Failed to retry issue #{session.github_issue_number}: {e}")

        if retried_count > 0:
            await self._emit_event("auto_retry", {
                "retried_count": retried_count,
            })

    async def _run_pr_status_check(self, task: ScheduledTask):
        """Check status of open PRs."""
        from .models import project_manager, issue_session_manager, IssueSessionStatus
        from .github_client import get_github_client

        # Get sessions with open PRs
        sessions_with_prs = [
            s for s in issue_session_manager.sessions.values()
            if s.status == IssueSessionStatus.PR_CREATED and s.pr_number
        ]

        for session in sessions_with_prs:
            project = project_manager.get(session.project_id)
            if not project or not project.github_token_encrypted:
                continue

            try:
                client = get_github_client(project.get_token())
                pr_info = await client.get_pull_request(project.github_repo, session.pr_number)

                if pr_info.get("merged"):
                    # PR was merged
                    issue_session_manager.update(
                        session.id,
                        status=IssueSessionStatus.COMPLETED,
                        completed_at=datetime.now().isoformat()
                    )
                    logger.info(f"PR #{session.pr_number} merged, marking complete")

                    await self._emit_event("pr_merged", {
                        "pr_number": session.pr_number,
                        "issue_number": session.github_issue_number,
                        "project_id": project.id,
                    })

                elif pr_info.get("state") == "closed":
                    # PR was closed without merge
                    issue_session_manager.update(
                        session.id,
                        status=IssueSessionStatus.FAILED,
                        last_error="PR closed without merge"
                    )
                    logger.info(f"PR #{session.pr_number} closed without merge")

            except Exception as e:
                logger.error(f"Failed to check PR #{session.pr_number}: {e}")

    async def _run_custom_task(self, task: ScheduledTask):
        """Run a custom task."""
        callback_name = task.config.get("callback")
        if callback_name and callback_name in self._callbacks:
            for callback in self._callbacks[callback_name]:
                await callback(task)

    # ==================== Events ====================

    def add_event_callback(self, event: str, callback: Callable):
        """Add callback for scheduler events."""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    async def _emit_event(self, event: str, data: dict):
        """Emit an event to all registered callbacks."""
        if event in self._callbacks:
            for callback in self._callbacks[event]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Event callback error: {e}")

    # ==================== Default Tasks ====================

    def setup_default_tasks(self):
        """Set up default scheduled tasks."""
        default_tasks = [
            ScheduledTask(
                id="global_issue_sync",
                name="Auto Issue Sync",
                task_type=TaskType.ISSUE_SYNC,
                schedule="15m",  # Every 15 minutes
                enabled=True,
            ),
            ScheduledTask(
                id="global_health_check",
                name="Session Health Check",
                task_type=TaskType.HEALTH_CHECK,
                schedule="5m",  # Every 5 minutes
                enabled=True,
            ),
            ScheduledTask(
                id="global_session_cleanup",
                name="Session Cleanup",
                task_type=TaskType.SESSION_CLEANUP,
                schedule="0 3 * * *",  # Daily at 3 AM
                enabled=True,
                config={"max_age_days": 7},
            ),
            ScheduledTask(
                id="global_auto_retry",
                name="Auto Retry Failed",
                task_type=TaskType.AUTO_RETRY,
                schedule="30m",  # Every 30 minutes
                enabled=True,
            ),
            ScheduledTask(
                id="global_pr_check",
                name="PR Status Check",
                task_type=TaskType.PR_STATUS_CHECK,
                schedule="10m",  # Every 10 minutes
                enabled=True,
            ),
        ]

        for task in default_tasks:
            if task.id not in self._tasks:
                self.add_task(task)

    def get_status(self) -> dict:
        """Get scheduler status summary."""
        return {
            "running": self._started,
            "task_count": len(self._tasks),
            "active_tasks": len([t for t in self._tasks.values() if t.enabled]),
            "tasks": [t.to_dict() for t in self._tasks.values()],
        }


# Global instance
task_scheduler = TaskScheduler()
