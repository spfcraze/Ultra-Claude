import asyncio
import os
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from typing import Set, Optional, List
from pydantic import BaseModel, Field

from .logging_config import setup_logging, get_logger
from .session_manager import manager, SessionStatus
from .models import (
    project_manager, issue_session_manager,
    Project, IssueSession, IssueSessionStatus, ProjectStatus, IssueFilter
)
from .github_client import get_github_client, GitHubError, GitHubAuthError, GitHubNotFoundError
from .workflow.api import router as workflow_router
from .browser.api import router as browser_router
from .browser.manager import browser_manager
from .scheduler import task_scheduler, ScheduledTask, TaskType
from .webhooks import webhook_handler, WebhookConfig
from .notifications import notification_manager, NotificationConfig, NotificationChannel, NotificationEvent, ChannelConfig
from .daemon import daemon_manager
from .auth import auth_config, get_current_user, require_auth, is_auth_enabled
from .security import (
    SecurityHeadersMiddleware, RateLimitMiddleware, HTTPSRedirectMiddleware,
    validate_path, is_safe_path, rate_limiter, get_cors_origins
)
from .audit import audit_logger, AuditEventType, get_client_ip
from .telegram.bot import telegram_bot

setup_logging()
logger = get_logger("autowrkers.server")

app = FastAPI(title="Autowrkers", version="0.3.0")

# Add security middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

# CORS - restrict allowed origins (configurable via AUTOWRKERS_CORS_ORIGINS env var)
from fastapi.middleware.cors import CORSMiddleware

cors_origins = get_cors_origins()
allow_all = "*" in cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all else cors_origins,
    allow_credentials=not allow_all,  # Cannot use credentials with wildcard origin
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)

# HTTPS redirect - enable via AUTOWRKERS_HTTPS_REDIRECT=true
if os.environ.get("AUTOWRKERS_HTTPS_REDIRECT", "").lower() in ("true", "1", "yes"):
    app.add_middleware(HTTPSRedirectMiddleware)

# Paths
BASE_DIR = Path(__file__).parent.parent
WEB_DIR = BASE_DIR / "web"

app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")

app.include_router(workflow_router)
app.include_router(browser_router)


class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)

        for conn in disconnected:
            self.active_connections.discard(conn)


ws_manager = ConnectionManager()


# Register callbacks with session manager
async def on_output(session_id: int, data: str):
    print(f"[DEBUG] Broadcasting output for session {session_id}: {len(data)} chars, {len(ws_manager.active_connections)} connections")
    await ws_manager.broadcast({
        "type": "output",
        "session_id": session_id,
        "data": data
    })


async def on_status_change(session_id: int, status: SessionStatus):
    session = manager.get_session(session_id)
    await ws_manager.broadcast({
        "type": "status",
        "session_id": session_id,
        "status": status.value,
        "session": session.to_dict() if session else None
    })


async def on_session_created(session):
    await ws_manager.broadcast({
        "type": "session_created",
        "session": session.to_dict()
    })


manager.add_output_callback(on_output)
manager.add_status_callback(on_status_change)
manager.add_session_created_callback(on_session_created)


async def on_automation_event(event_type: str, data: dict):
    await ws_manager.broadcast({
        "type": "automation_event",
        "event": event_type,
        "data": data
    })


from .automation import automation_controller
automation_controller.add_event_callback(on_automation_event)


# Browser automation callbacks
async def on_browser_status(session_id: str, status):
    await ws_manager.broadcast({
        "type": "browser_status",
        "session_id": session_id,
        "status": status.value,
    })

async def on_browser_action(session_id: str, action):
    await ws_manager.broadcast({
        "type": "browser_action",
        "session_id": session_id,
        "action": action.to_dict(),
    })

browser_manager.add_status_callback(on_browser_status)
browser_manager.add_action_callback(on_browser_action)


@app.on_event("startup")
async def startup_event():
    """Recover state and start output readers for any reconnected tmux sessions"""
    await manager.start_output_readers()
    await automation_controller.recover_interrupted_sessions()

    from .workflow.engine import workflow_orchestrator
    await workflow_orchestrator.recover_interrupted_executions()

    # Start the task scheduler
    task_scheduler.setup_default_tasks()
    task_scheduler.start()
    logger.info("Task scheduler started with default tasks")

    # Set up notification callbacks for automation events
    async def on_notification_event(event_type: str, data: dict):
        """Forward automation events to notification system"""
        event_map = {
            "issue_started": NotificationEvent.ISSUE_STARTED,
            "issue_completed": NotificationEvent.ISSUE_COMPLETED,
            "issue_failed": NotificationEvent.ISSUE_FAILED,
            "pr_created": NotificationEvent.PR_CREATED,
            "session_error": NotificationEvent.SESSION_ERROR,
        }
        if event_type in event_map:
            await notification_manager.notify(event_map[event_type], data)

    automation_controller.add_event_callback(on_notification_event)

    # Start Telegram bot if configured
    if telegram_bot.get_config().enabled and telegram_bot.get_config().bot_token:
        try:
            await telegram_bot.start()
            # Register callbacks for event relay
            manager.add_status_callback(telegram_bot._on_session_status)
            manager.add_completion_callback(telegram_bot._on_session_complete)
            automation_controller.add_event_callback(telegram_bot._on_automation_event)
            logger.info("Telegram bot started")
        except Exception as e:
            logger.error(f"Failed to start Telegram bot: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Graceful shutdown"""
    # Save session state first, before reader tasks are cancelled,
    # so running sessions persist and reconnect on restart
    manager._save_sessions()
    logger.info("Session state saved")

    task_scheduler.stop()
    logger.info("Task scheduler stopped")
    await browser_manager.close_all()
    logger.info("Browser sessions closed")
    # Stop Telegram bot
    try:
        await telegram_bot.stop()
        logger.info("Telegram bot stopped")
    except Exception as e:
        logger.error(f"Error stopping Telegram bot: {e}")


@app.get("/health")
async def health_check():
    import shutil
    
    tmux_available = shutil.which("tmux") is not None
    
    try:
        sessions = manager.get_all_sessions()
        session_manager_ok = True
        active_sessions = len([s for s in sessions if s.status == SessionStatus.RUNNING])
        total_sessions = len(sessions)
    except Exception:
        session_manager_ok = False
        active_sessions = 0
        total_sessions = 0
    
    try:
        projects = project_manager.get_all()
        project_manager_ok = True
        total_projects = len(projects)
    except Exception:
        project_manager_ok = False
        total_projects = 0
    
    try:
        issue_sessions = list(issue_session_manager.sessions.values())
        issue_session_manager_ok = True
        pending_issues = len([s for s in issue_sessions if s.status == IssueSessionStatus.PENDING])
        in_progress_issues = len([s for s in issue_sessions if s.status == IssueSessionStatus.IN_PROGRESS])
    except Exception:
        issue_session_manager_ok = False
        pending_issues = 0
        in_progress_issues = 0
    
    automation_ok = automation_controller is not None
    
    all_ok = all([
        tmux_available,
        session_manager_ok,
        project_manager_ok,
        issue_session_manager_ok,
        automation_ok
    ])
    
    return {
        "status": "healthy" if all_ok else "degraded",
        "components": {
            "tmux": {"status": "ok" if tmux_available else "error", "available": tmux_available},
            "session_manager": {
                "status": "ok" if session_manager_ok else "error",
                "active_sessions": active_sessions,
                "total_sessions": total_sessions
            },
            "project_manager": {
                "status": "ok" if project_manager_ok else "error",
                "total_projects": total_projects
            },
            "issue_session_manager": {
                "status": "ok" if issue_session_manager_ok else "error",
                "pending_issues": pending_issues,
                "in_progress_issues": in_progress_issues
            },
            "automation_controller": {
                "status": "ok" if automation_ok else "error"
            }
        },
        "websocket_connections": len(ws_manager.active_connections)
    }


from .updater import updater


@app.get("/api/update/check")
async def check_for_updates():
    update_info = await updater.check_for_updates()
    git_status = await updater.get_local_git_status()
    return {
        "update": update_info.to_dict(),
        "git": git_status,
    }


@app.post("/api/update/install")
async def install_update(force: bool = False):
    update_info = await updater.check_for_updates()
    if not update_info.update_available and not force:
        return {
            "success": False,
            "error": "No update available",
            "current_version": update_info.current_version,
        }
    
    result = await updater.update(force=force)
    return result


@app.get("/api/version")
async def get_version():
    from src import __version__
    return {
        "version": __version__,
        "repo": "https://github.com/spfcraze/Ultra-Claude",
    }


@app.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring."""
    from src import __version__
    return {
        "status": "healthy",
        "version": __version__,
        "scheduler": task_scheduler.is_running() if task_scheduler else False,
        "websocket_connections": len(ws_manager.active_connections),
        "auth_enabled": is_auth_enabled(),
    }


# ==================== Authentication Endpoints ====================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


@app.get("/api/auth/status")
async def auth_status():
    """Get authentication status."""
    return {
        "enabled": is_auth_enabled(),
        "has_users": auth_config.has_users(),
        "jwt_available": auth_config._jwt_secret is not None,
    }


@app.post("/api/auth/setup")
async def auth_setup(request_data: RegisterRequest, request: Request):
    """
    Set up the first admin user.
    Only works when no users exist.
    """
    client_ip = get_client_ip(request)

    if auth_config.has_users():
        raise HTTPException(
            status_code=400,
            detail="Users already exist. Use login instead."
        )

    if not auth_config.create_user(request_data.username, request_data.password):
        raise HTTPException(status_code=500, detail="Failed to create user")

    # Generate tokens
    access_token = auth_config.create_access_token(request_data.username)
    refresh_token = auth_config.create_refresh_token(request_data.username)

    audit_logger.log(
        AuditEventType.AUTH_SETUP,
        source_ip=client_ip,
        username=request_data.username,
        details={"action": "first_user_created"},
    )

    return {
        "success": True,
        "message": "Admin user created successfully",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@app.post("/api/auth/login")
async def auth_login(request_data: LoginRequest, request: Request):
    """Authenticate and get access token."""
    client_ip = get_client_ip(request)

    if not auth_config.authenticate(request_data.username, request_data.password):
        audit_logger.log(
            AuditEventType.AUTH_LOGIN_FAILED,
            source_ip=client_ip,
            username=request_data.username,
            success=False,
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    access_token = auth_config.create_access_token(request_data.username)
    refresh_token = auth_config.create_refresh_token(request_data.username)

    audit_logger.log(
        AuditEventType.AUTH_LOGIN_SUCCESS,
        source_ip=client_ip,
        username=request_data.username,
    )

    return {
        "success": True,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@app.post("/api/auth/refresh")
async def auth_refresh(request: Request):
    """Refresh access token using refresh token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    refresh_token = auth_header[7:]
    username = auth_config.verify_token(refresh_token, token_type="refresh")

    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    access_token = auth_config.create_access_token(username)

    return {
        "success": True,
        "access_token": access_token,
        "token_type": "bearer"
    }


@app.get("/api/auth/me")
async def auth_me(current_user: str = Depends(get_current_user)):
    """Get current user info."""
    if current_user is None:
        return {"authenticated": False, "auth_enabled": False}
    return {"authenticated": True, "username": current_user}


@app.post("/api/auth/change-password")
async def auth_change_password(
    request: ChangePasswordRequest,
    current_user: str = Depends(require_auth)
):
    """Change current user's password."""
    if not auth_config.authenticate(current_user, request.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    if len(request.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    if not auth_config.update_password(current_user, request.new_password):
        raise HTTPException(status_code=500, detail="Failed to update password")

    return {"success": True, "message": "Password updated successfully"}


@app.post("/api/auth/enable")
async def auth_enable(current_user: str = Depends(require_auth)):
    """Enable authentication (requires existing auth)."""
    auth_config.enable()
    return {"success": True, "message": "Authentication enabled"}


@app.post("/api/auth/disable")
async def auth_disable(current_user: str = Depends(require_auth)):
    """Disable authentication (requires existing auth)."""
    auth_config.disable()
    return {"success": True, "message": "Authentication disabled"}


@app.get("/api/audit/log")
async def get_audit_log(
    limit: int = 100,
    event_type: Optional[str] = None,
    current_user: str = Depends(get_current_user)
):
    """Get recent audit log entries."""
    entries = audit_logger.get_recent(limit=min(limit, 500), event_type=event_type)
    return {"entries": entries, "count": len(entries)}


@app.get("/api/audit/failed-logins")
async def get_failed_logins(
    limit: int = 50,
    current_user: str = Depends(get_current_user)
):
    """Get recent failed login attempts."""
    entries = audit_logger.get_failed_logins(limit=min(limit, 200))
    return {"entries": entries, "count": len(entries)}


@app.get("/api/server/info")
async def get_server_info():
    """Get server information including the current working directory.

    This is useful for auto-filling the project working directory field
    when Claude Code is run from within a project directory.
    """
    import os
    from pathlib import Path

    cwd = os.getcwd()
    if not os.path.isdir(cwd):
        cwd = str(Path.home())

    # Check if current directory is a git repo
    is_git_repo = os.path.isdir(os.path.join(cwd, ".git"))

    # Try to detect the repo name from git remote
    repo_name = None
    if is_git_repo:
        try:
            import subprocess
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                remote_url = result.stdout.strip()
                # Parse owner/repo from various URL formats
                # https://github.com/owner/repo.git
                # git@github.com:owner/repo.git
                if "github.com" in remote_url:
                    if remote_url.startswith("git@"):
                        # git@github.com:owner/repo.git
                        repo_name = remote_url.split(":")[-1].replace(".git", "")
                    else:
                        # https://github.com/owner/repo.git
                        parts = remote_url.replace(".git", "").split("/")
                        if len(parts) >= 2:
                            repo_name = f"{parts[-2]}/{parts[-1]}"
        except Exception:
            pass

    return {
        "working_directory": cwd,
        "is_git_repo": is_git_repo,
        "detected_repo": repo_name,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "sessions": [s.to_dict() for s in manager.get_all_sessions()]
    })


@app.get("/api/sessions")
async def get_sessions():
    return {"sessions": [s.to_dict() for s in manager.get_all_sessions()]}


@app.post("/api/sessions")
async def create_session(
    name: str = None,
    working_dir: str = None,
    parent_id: int = None,
    initial_prompt: str = None
):
    """
    Create a new session.

    - parent_id: If specified, this session will be queued until the parent completes
    - initial_prompt: If specified, this prompt will be sent to Claude after startup
    """
    try:
        session = manager.create_session(
            name=name,
            working_dir=working_dir,
            parent_id=parent_id,
            initial_prompt=initial_prompt
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    success = await manager.start_session(session)
    return {
        "success": success,
        "session": session.to_dict()
    }


@app.post("/api/sessions/{session_id}/input")
async def send_input(session_id: int, data: str):
    # Auto-add carriage return if not present (to press Enter)
    if not data.endswith('\r'):
        data = data + '\r'
    success = await manager.send_input(session_id, data)
    return {"success": success}


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: int):
    success = await manager.stop_session(session_id)
    return {"success": success}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: int):
    """Delete a session completely (stops it first if running)"""
    success = await manager.remove_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"success": success}


@app.post("/api/sessions/{session_id}/complete")
async def complete_session(session_id: int):
    """Mark a session as completed, which will trigger any queued child sessions to start"""
    success = await manager.mark_session_completed(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"success": success}


@app.post("/api/sessions/{session_id}/parent")
async def update_session_parent(session_id: int, request: Request):
    """Update a session's parent (for Kanban drag & drop and context menu)"""
    try:
        body = await request.json()
        parent_id = body.get("parent_id")
    except Exception:
        parent_id = None
    success = await manager.update_session_parent(session_id, parent_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update parent")
    session = manager.get_session(session_id)
    return {"success": success, "session": session.to_dict() if session else None}


@app.get("/api/sessions/queued")
async def get_queued_sessions(parent_id: int = None):
    """Get all queued sessions, optionally filtered by parent"""
    sessions = manager.get_queued_sessions(parent_id)
    return {"sessions": [s.to_dict() for s in sessions]}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: int):
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": session.to_dict()}


@app.get("/api/sessions/{session_id}/output")
async def get_session_output(session_id: int):
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"output": manager.get_session_output(session_id)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)

    # Send current state
    await websocket.send_json({
        "type": "init",
        "sessions": [s.to_dict() for s in manager.get_all_sessions()]
    })

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "input":
                session_id = data.get("session_id")
                input_data = data.get("data", "")
                await manager.send_input(session_id, input_data)

            elif data.get("type") == "create":
                name = data.get("name")
                working_dir = data.get("working_dir")
                parent_id = data.get("parent_id")
                initial_prompt = data.get("initial_prompt")
                create_dir = data.get("create_dir", False)
                try:
                    session = manager.create_session(
                        name=name,
                        working_dir=working_dir,
                        parent_id=parent_id,
                        initial_prompt=initial_prompt,
                        create_dir=create_dir,
                    )
                    await manager.start_session(session)
                except ValueError as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": str(e)
                    })

            elif data.get("type") == "stop":
                session_id = data.get("session_id")
                await manager.stop_session(session_id)

            elif data.get("type") == "complete":
                session_id = data.get("session_id")
                await manager.mark_session_completed(session_id)

            elif data.get("type") == "update_parent":
                session_id = data.get("session_id")
                parent_id = data.get("parent_id")  # None to remove parent
                await manager.update_session_parent(session_id, parent_id)

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ==================== Pydantic Models for Request Bodies ====================

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    github_repo: str = Field("", max_length=200)
    github_token: str = Field("", max_length=500)
    working_dir: str = Field("", max_length=1000)
    default_branch: str = Field("main", max_length=100)
    auto_sync: bool = True
    auto_start: bool = False
    verification_command: str = Field("", max_length=2000)
    lint_command: str = Field("", max_length=2000)
    build_command: str = Field("", max_length=2000)
    test_command: str = Field("", max_length=2000)
    max_concurrent: int = Field(1, ge=1, le=20)
    issue_filter: Optional[dict] = None
    # LLM Provider settings
    llm_provider: str = Field("claude_code", max_length=50)
    llm_model: str = Field("", max_length=200)
    llm_api_url: str = Field("", max_length=500)
    llm_api_key: str = Field("", max_length=500)
    llm_context_length: int = Field(8192, ge=1024, le=2000000)
    llm_temperature: float = Field(0.1, ge=0.0, le=2.0)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    github_token: Optional[str] = Field(None, max_length=500)
    working_dir: Optional[str] = Field(None, max_length=1000)
    default_branch: Optional[str] = Field(None, max_length=100)
    auto_sync: Optional[bool] = None
    auto_start: Optional[bool] = None
    verification_command: Optional[str] = Field(None, max_length=2000)
    lint_command: Optional[str] = Field(None, max_length=2000)
    build_command: Optional[str] = Field(None, max_length=2000)
    test_command: Optional[str] = Field(None, max_length=2000)
    max_concurrent: Optional[int] = Field(None, ge=1, le=20)
    issue_filter: Optional[dict] = None
    # LLM Provider settings
    llm_provider: Optional[str] = Field(None, max_length=50)
    llm_model: Optional[str] = Field(None, max_length=200)
    llm_api_url: Optional[str] = Field(None, max_length=500)
    llm_api_key: Optional[str] = Field(None, max_length=500)
    llm_context_length: Optional[int] = Field(None, ge=1024, le=2000000)
    llm_temperature: Optional[float] = Field(None, ge=0.0, le=2.0)


class LLMTestRequest(BaseModel):
    """Request body for testing LLM connection"""
    provider: str = Field(..., max_length=50)
    api_url: str = Field("", max_length=500)
    api_key: str = Field("", max_length=500)
    model_name: str = Field("", max_length=200)


# ==================== Project API Endpoints ====================

@app.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    return templates.TemplateResponse("projects.html", {
        "request": request,
        "projects": [p.to_dict() for p in project_manager.get_all()]
    })


@app.get("/issues", response_class=HTMLResponse)
async def issues_page(request: Request):
    return templates.TemplateResponse("issues.html", {"request": request})


@app.get("/workflows", response_class=HTMLResponse)
async def workflows_page(request: Request):
    return templates.TemplateResponse("workflows.html", {"request": request})


@app.get("/browser", response_class=HTMLResponse)
async def browser_page(request: Request):
    return templates.TemplateResponse("browser.html", {"request": request})


@app.get("/api/browse-dirs")
async def browse_directories(path: str = "~"):
    import os

    if path == "~":
        path = os.path.expanduser("~")

    # Validate path to prevent traversal attacks
    try:
        path = validate_path(path)
    except HTTPException:
        return {"error": "Access denied", "path": path, "dirs": [], "parent": None}

    if not os.path.exists(path):
        return {"error": "Path does not exist", "path": path, "dirs": [], "parent": None}

    if not os.path.isdir(path):
        path = os.path.dirname(path)

    try:
        entries = os.listdir(path)
    except PermissionError:
        return {"error": "Permission denied", "path": path, "dirs": [], "parent": os.path.dirname(path)}
    
    dirs = []
    for entry in sorted(entries):
        if entry.startswith('.'):
            continue
        full_path = os.path.join(path, entry)
        if os.path.isdir(full_path):
            is_git = os.path.isdir(os.path.join(full_path, ".git"))
            dirs.append({"name": entry, "path": full_path, "is_git": is_git})
    
    parent = os.path.dirname(path) if path != "/" else None
    
    return {
        "path": path,
        "dirs": dirs,
        "parent": parent,
    }


@app.get("/api/projects")
async def get_projects():
    """Get all projects"""
    return {"projects": [p.to_dict() for p in project_manager.get_all()]}


def normalize_github_repo(repo: str) -> str:
    """Normalize GitHub repo to owner/repo format"""
    repo = repo.strip()
    # Remove common URL prefixes
    prefixes = [
        "https://github.com/",
        "http://github.com/",
        "github.com/",
        "www.github.com/",
    ]
    for prefix in prefixes:
        if repo.lower().startswith(prefix):
            repo = repo[len(prefix):]
            break
    # Remove trailing slashes and .git
    repo = repo.rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return repo


@app.post("/api/projects")
async def create_project(project: ProjectCreate):
    """Create a new project"""
    # Normalize the repo format (handle full URLs)
    github_repo = normalize_github_repo(project.github_repo)

    # Validate repo format
    if "/" not in github_repo or github_repo.count("/") != 1:
        raise HTTPException(status_code=400, detail="Invalid repository format. Use 'owner/repo' format (e.g., 'spfcraze/WP-booking-pro')")

    # Validate GitHub token if provided
    if project.github_token:
        client = get_github_client(project.github_token)
        try:
            if not await client.verify_access(github_repo):
                raise HTTPException(status_code=400, detail="Cannot access repository. Check that the repo exists and your token has access.")
        except GitHubAuthError:
            raise HTTPException(status_code=401, detail="Invalid GitHub token")
        except GitHubError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Create project
    issue_filter = IssueFilter.from_dict(project.issue_filter) if project.issue_filter else IssueFilter()

    new_project = project_manager.create(
        name=project.name,
        github_repo=github_repo,
        github_token=project.github_token,
        working_dir=project.working_dir,
        default_branch=project.default_branch,
        issue_filter=issue_filter,
        auto_sync=project.auto_sync,
        auto_start=project.auto_start,
        verification_command=project.verification_command,
        lint_command=project.lint_command,
        build_command=project.build_command,
        test_command=project.test_command,
        max_concurrent=project.max_concurrent,
        llm_provider=project.llm_provider,
        llm_model=project.llm_model,
        llm_api_url=project.llm_api_url,
        llm_context_length=project.llm_context_length,
        llm_temperature=project.llm_temperature,
    )

    # Set LLM API key if provided (encrypted separately)
    if project.llm_api_key:
        new_project.set_llm_api_key(project.llm_api_key)
        project_manager.save()

    return {"success": True, "project": new_project.to_dict()}


@app.get("/api/projects/{project_id}")
async def get_project(project_id: int):
    """Get a project by ID"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": project.to_dict()}


@app.put("/api/projects/{project_id}")
async def update_project(project_id: int, updates: ProjectUpdate):
    """Update a project"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = updates.dict(exclude_unset=True)

    # Handle issue_filter separately
    if "issue_filter" in update_data and update_data["issue_filter"]:
        update_data["issue_filter"] = IssueFilter.from_dict(update_data["issue_filter"])

    # Handle LLM API key separately (needs encryption)
    llm_api_key = update_data.pop("llm_api_key", None)

    updated = project_manager.update(project_id, **update_data)

    # Set LLM API key if provided
    if llm_api_key is not None:
        if llm_api_key:  # Non-empty string - encrypt and store
            updated.set_llm_api_key(llm_api_key)
        else:  # Empty string - clear the key
            updated.llm_api_key_encrypted = ""
        project_manager.save()

    return {"success": True, "project": updated.to_dict()}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: int):
    """Delete a project"""
    if not project_manager.delete(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True}


@app.get("/api/projects/{project_id}/test-token")
async def test_project_token(project_id: int):
    """Test if the project's GitHub token has proper access"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not project.github_token_encrypted:
        return {"success": False, "error": "No token configured"}

    token = project.get_token()
    client = get_github_client(token)

    results = {
        "token_prefix": token[:8] + "..." if len(token) > 8 else "***",
        "repo": project.github_repo,
        "checks": {}
    }

    # Test 1: Can we get the authenticated user?
    try:
        user_info = await client._request("GET", "/user")
        results["checks"]["auth"] = {"success": True, "user": user_info.get("login")}
    except Exception as e:
        results["checks"]["auth"] = {"success": False, "error": str(e)}

    # Test 2: Can we access the repository?
    try:
        repo_info = await client._request("GET", f"/repos/{project.github_repo}")
        results["checks"]["repo_read"] = {
            "success": True,
            "private": repo_info.get("private"),
            "permissions": repo_info.get("permissions", {})
        }
    except Exception as e:
        results["checks"]["repo_read"] = {"success": False, "error": str(e)}

    # Test 3: Check token scopes from response headers (if available)
    try:
        # Make a simple request to check scopes
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.github.com/user",
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
            ) as resp:
                scopes = resp.headers.get("X-OAuth-Scopes", "")
                results["checks"]["scopes"] = {"success": True, "scopes": scopes}
    except Exception as e:
        results["checks"]["scopes"] = {"success": False, "error": str(e)}

    # Overall success
    results["success"] = all(
        c.get("success", False) for c in results["checks"].values()
    )

    if not results["success"]:
        results["hint"] = "Token needs 'repo' scope for full access to private repositories"

    return results


# ==================== GitHub Sync Endpoints ====================

@app.post("/api/projects/{project_id}/sync")
async def sync_project_issues(project_id: int):
    """Sync issues from GitHub for a project"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not project.github_token_encrypted:
        raise HTTPException(status_code=400, detail="Project has no GitHub token configured")

    client = get_github_client(project.get_token())

    try:
        issues = await client.get_all_issues(
            project.github_repo,
            project.issue_filter if isinstance(project.issue_filter, IssueFilter) else None
        )
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Create issue sessions for new issues
    created = []
    existing = []
    for issue in issues:
        existing_session = issue_session_manager.get_by_issue(project_id, issue.number)
        if existing_session:
            existing.append(existing_session.to_dict())
        else:
            session = issue_session_manager.create(project_id, issue)
            created.append(session.to_dict())

    # Update last sync time
    from datetime import datetime
    project_manager.update(project_id, last_sync=datetime.now().isoformat())

    return {
        "success": True,
        "synced": len(issues),
        "created": len(created),
        "existing": len(existing),
        "issue_sessions": created + existing
    }


# ==================== Git Repository Endpoints ====================

@app.get("/api/projects/{project_id}/git/status")
async def get_git_status(project_id: int):
    """Check the git repository status for a project"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    working_dir = project.working_dir
    if not working_dir:
        return {
            "status": "not_configured",
            "message": "Working directory not configured",
            "is_git_repo": False,
            "remote_url": None,
            "current_branch": None,
            "is_clean": None,
            "ahead_behind": None
        }

    import os
    import subprocess

    # Check if directory exists
    if not os.path.isdir(working_dir):
        return {
            "status": "missing",
            "message": f"Directory does not exist: {working_dir}",
            "is_git_repo": False,
            "remote_url": None,
            "current_branch": None,
            "is_clean": None,
            "ahead_behind": None
        }

    # Check if it's a git repo
    git_dir = os.path.join(working_dir, ".git")
    if not os.path.isdir(git_dir):
        return {
            "status": "not_initialized",
            "message": "Directory exists but is not a git repository",
            "is_git_repo": False,
            "remote_url": None,
            "current_branch": None,
            "is_clean": None,
            "ahead_behind": None
        }

    # Get remote URL
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=working_dir,
            capture_output=True,
            text=True
        )
        remote_url = result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        remote_url = None

    # Get current branch
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=working_dir,
            capture_output=True,
            text=True
        )
        current_branch = result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        current_branch = None

    # Check if working tree is clean
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=working_dir,
            capture_output=True,
            text=True
        )
        is_clean = len(result.stdout.strip()) == 0 if result.returncode == 0 else None
    except Exception:
        is_clean = None

    # Check ahead/behind status
    ahead_behind = None
    try:
        result = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", f"HEAD...origin/{project.default_branch}"],
            cwd=working_dir,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) == 2:
                ahead_behind = {"ahead": int(parts[0]), "behind": int(parts[1])}
    except Exception:
        pass

    # Determine overall status
    expected_remote = f"https://github.com/{project.github_repo}.git"
    expected_remote_ssh = f"git@github.com:{project.github_repo}.git"

    if remote_url and (expected_remote in remote_url or expected_remote_ssh in remote_url or project.github_repo in remote_url):
        status = "ready"
        message = "Repository is set up correctly"
    elif remote_url:
        status = "wrong_remote"
        message = f"Remote URL doesn't match expected repository"
    else:
        status = "no_remote"
        message = "No remote configured"

    return {
        "status": status,
        "message": message,
        "is_git_repo": True,
        "remote_url": remote_url,
        "expected_remote": expected_remote,
        "current_branch": current_branch,
        "default_branch": project.default_branch,
        "is_clean": is_clean,
        "ahead_behind": ahead_behind
    }


@app.post("/api/projects/{project_id}/git/setup")
async def setup_git_repository(project_id: int):
    """Clone or update the git repository for a project"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not project.github_token_encrypted:
        raise HTTPException(status_code=400, detail="Project has no GitHub token configured")

    working_dir = project.working_dir
    if not working_dir:
        raise HTTPException(status_code=400, detail="Working directory not configured")

    import os
    import subprocess

    token = project.get_token()
    # Use x-access-token format which works with both classic and fine-grained PATs
    clone_url = f"https://x-access-token:{token}@github.com/{project.github_repo}.git"
    safe_clone_url = f"https://x-access-token:***@github.com/{project.github_repo}.git"  # For logging

    # First, verify token has access to the repository
    try:
        client = get_github_client(token)
        # Quick check to verify token works
        import asyncio
        loop = asyncio.get_event_loop()
        # Verify access synchronously within the async context
    except Exception as e:
        return {
            "success": False,
            "action": "verify",
            "message": f"Token verification failed. Please update your GitHub token in project settings with 'repo' scope. Error: {str(e)}"
        }

    # Ensure parent directory exists
    parent_dir = os.path.dirname(working_dir)
    if parent_dir and not os.path.exists(parent_dir):
        try:
            os.makedirs(parent_dir, exist_ok=True)
            print(f"[Git Setup] Created parent directory: {parent_dir}")
        except PermissionError:
            return {
                "success": False,
                "action": "mkdir",
                "message": f"Permission denied creating directory: {parent_dir}. Please check permissions or choose a different path.",
                "suggested_path": f"/home/{os.environ.get('USER', 'user')}/repos/{project.name}"
            }
        except Exception as e:
            return {
                "success": False,
                "action": "mkdir",
                "message": f"Failed to create directory {parent_dir}: {str(e)}"
            }

    # Check if directory exists
    if os.path.isdir(working_dir):
        git_dir = os.path.join(working_dir, ".git")
        if os.path.isdir(git_dir):
            # It's a git repo - fetch and pull latest using credential helper
            from .git_credentials import secure_credential_helper, git_env

            try:
                with secure_credential_helper(token) as credential_helper:
                    # Fetch from remote with credentials
                    subprocess.run(
                        ["git", "-c", f"credential.helper=!{credential_helper}", "fetch", "origin"],
                        cwd=working_dir,
                        capture_output=True,
                        text=True,
                        timeout=60,
                        env=git_env()
                    )

                    # Checkout default branch
                    subprocess.run(
                        ["git", "checkout", project.default_branch],
                        cwd=working_dir,
                        capture_output=True,
                        text=True
                    )

                    # Pull latest with credentials
                    result = subprocess.run(
                        ["git", "-c", f"credential.helper=!{credential_helper}", "pull", "origin", project.default_branch],
                        cwd=working_dir,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env=git_env()
                    )

                    if result.returncode != 0:
                        error_msg = result.stderr.replace(token, "***")
                        return {
                            "success": False,
                            "action": "pull",
                            "message": f"Failed to pull latest changes: {error_msg}"
                        }

                    return {
                        "success": True,
                        "action": "updated",
                        "message": f"Repository updated successfully",
                        "output": result.stdout
                    }
            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "action": "pull",
                    "message": "Operation timed out"
                }
            except Exception as e:
                return {
                    "success": False,
                    "action": "pull",
                    "message": str(e)
                }
        else:
            # Directory exists but is not a git repo
            # We should not overwrite - inform user
            return {
                "success": False,
                "action": "none",
                "message": f"Directory exists but is not a git repository. Please remove it or choose a different working directory."
            }
    else:
        # Clone the repository
        try:
            # Create parent directory if needed
            parent_dir = os.path.dirname(working_dir)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir)

            from .git_credentials import secure_credential_helper, git_env

            with secure_credential_helper(token) as credential_helper:
                # Clone using the credential helper
                https_url = f"https://github.com/{project.github_repo}.git"
                result = subprocess.run(
                    ["git", "clone",
                     "-c", f"credential.helper=!{credential_helper}",
                     https_url, working_dir],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=git_env()
                )

            if result.returncode != 0:
                # Sanitize error message to not expose token
                error_msg = result.stderr.replace(token, "***")

                # Detect common token permission issues
                if "403" in error_msg or "Write access" in error_msg or "Permission denied" in error_msg:
                    return {
                        "success": False,
                        "action": "clone",
                        "message": "Token permission error: Your GitHub token doesn't have access to this repository.",
                        "hint": "Please update your token in project Settings with the 'repo' scope enabled.",
                        "details": error_msg
                    }
                elif "401" in error_msg or "Authentication" in error_msg:
                    return {
                        "success": False,
                        "action": "clone",
                        "message": "Authentication failed: Your GitHub token may be invalid or expired.",
                        "hint": "Please generate a new token and update it in project Settings.",
                        "details": error_msg
                    }
                elif "404" in error_msg or "not found" in error_msg.lower():
                    return {
                        "success": False,
                        "action": "clone",
                        "message": "Repository not found: Check the repository name or token permissions for private repos.",
                        "hint": "For private repositories, ensure your token has 'repo' scope.",
                        "details": error_msg
                    }

                return {
                    "success": False,
                    "action": "clone",
                    "message": f"Failed to clone repository: {error_msg}"
                }

            return {
                "success": True,
                "action": "cloned",
                "message": f"Repository cloned successfully to {working_dir}"
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "action": "clone",
                "message": "Clone operation timed out (exceeded 5 minutes)"
            }
        except Exception as e:
            return {
                "success": False,
                "action": "clone",
                "message": str(e)
            }


@app.post("/api/projects/{project_id}/git/pull")
async def pull_git_repository(project_id: int):
    """Pull latest changes from remote"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    working_dir = project.working_dir
    if not working_dir:
        raise HTTPException(status_code=400, detail="Working directory not configured")

    import os
    import subprocess

    if not os.path.isdir(os.path.join(working_dir, ".git")):
        raise HTTPException(status_code=400, detail="Not a git repository. Use setup endpoint first.")

    token = project.get_token()
    if not token:
        raise HTTPException(status_code=400, detail="No GitHub token configured")

    from .git_credentials import secure_credential_helper, git_env

    try:
        with secure_credential_helper(token) as credential_helper:
            # Fetch with credentials
            subprocess.run(
                ["git", "-c", f"credential.helper=!{credential_helper}", "fetch", "origin"],
                cwd=working_dir,
                capture_output=True,
                timeout=60,
                env=git_env()
            )

            # Pull with credentials
            result = subprocess.run(
                ["git", "-c", f"credential.helper=!{credential_helper}", "pull", "origin", project.default_branch],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=120,
                env=git_env()
            )

            if result.returncode != 0:
                error_msg = result.stderr.replace(token, "***")
                return {"success": False, "message": error_msg}

            return {"success": True, "message": "Pulled latest changes", "output": result.stdout}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Pull operation timed out"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/projects/{project_id}/issues")
async def get_project_issues(project_id: int, status: str = None):
    """Get all issue sessions for a project"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    sessions = issue_session_manager.get_by_project(project_id)

    if status:
        try:
            status_filter = IssueSessionStatus(status)
            sessions = [s for s in sessions if s.status == status_filter]
        except ValueError:
            pass

    return {"issue_sessions": [s.to_dict() for s in sessions]}


# ==================== Issue Session Endpoints ====================

@app.get("/api/issue-sessions")
async def get_all_issue_sessions():
    """Get all issue sessions"""
    sessions = list(issue_session_manager.sessions.values())
    return {"issue_sessions": [s.to_dict() for s in sessions]}


@app.get("/api/issue-sessions/{session_id}")
async def get_issue_session(session_id: int):
    """Get an issue session by ID"""
    session = issue_session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Issue session not found")
    return {"issue_session": session.to_dict()}


@app.post("/api/issue-sessions/{session_id}/start")
async def start_issue_session(session_id: int):
    """Start working on an issue (creates Autowrkers session)"""
    from .automation import automation_controller
    issue_session = issue_session_manager.get(session_id)
    if not issue_session:
        raise HTTPException(status_code=404, detail="Issue session not found")

    if issue_session.status not in (IssueSessionStatus.PENDING, IssueSessionStatus.FAILED):
        raise HTTPException(status_code=400, detail=f"Cannot start session in {issue_session.status.value} status")

    try:
        await automation_controller.start_issue_session(issue_session)
        return {"success": True, "issue_session": issue_session.to_dict()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/issue-sessions/{session_id}/retry")
async def retry_issue_session(session_id: int):
    """Retry a failed issue session"""
    issue_session = issue_session_manager.get(session_id)
    if not issue_session:
        raise HTTPException(status_code=404, detail="Issue session not found")

    if issue_session.status not in [IssueSessionStatus.FAILED, IssueSessionStatus.IN_PROGRESS]:
        raise HTTPException(status_code=400, detail="Can only retry failed or stuck in_progress sessions")

    # Reset status - clear linked session manually to avoid parameter conflict
    issue_session = issue_session_manager.get(session_id)
    if issue_session:
        issue_session.session_id = None  # Clear any linked Autowrkers session

    issue_session_manager.update(
        session_id,
        status=IssueSessionStatus.PENDING,
        last_error="",
        attempts=0  # Reset attempt counter for retry
    )

    return {"success": True, "issue_session": issue_session.to_dict()}


@app.post("/api/issue-sessions/{session_id}/skip")
async def skip_issue_session(session_id: int):
    """Skip an issue session"""
    issue_session = issue_session_manager.get(session_id)
    if not issue_session:
        raise HTTPException(status_code=404, detail="Issue session not found")

    issue_session_manager.update(session_id, status=IssueSessionStatus.SKIPPED)
    return {"success": True, "issue_session": issue_session.to_dict()}


# ==================== Automation Control Endpoints ====================

@app.post("/api/projects/{project_id}/automation/start")
async def start_automation(project_id: int):
    """Start automation for a project"""
    from .automation import automation_controller

    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await automation_controller.start_project(project_id)
    project_manager.update(project_id, status=ProjectStatus.RUNNING)

    return {"success": True, "status": "running"}


@app.post("/api/projects/{project_id}/automation/stop")
async def stop_automation(project_id: int):
    """Stop automation for a project"""
    from .automation import automation_controller

    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await automation_controller.stop_project(project_id)
    project_manager.update(project_id, status=ProjectStatus.PAUSED)

    return {"success": True, "status": "paused"}


@app.get("/api/projects/{project_id}/automation/status")
async def get_automation_status(project_id: int):
    """Get automation status for a project"""
    from .automation import automation_controller

    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    status = automation_controller.get_project_status(project_id)
    return {
        "project_id": project_id,
        "status": project.status.value,
        "automation": status
    }


@app.get("/api/projects/{project_id}/automation/logs")
async def get_automation_logs(project_id: int, limit: int = 50):
    """Get automation logs for a project"""
    from .automation import automation_controller

    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    logs = automation_controller.get_project_logs(project_id, limit)
    return {
        "project_id": project_id,
        "logs": logs
    }


# ==================== LLM Provider Endpoints ====================

@app.post("/api/llm/test")
async def test_llm_connection(request: LLMTestRequest):
    """Test connection to an LLM provider"""
    from .llm_provider import LLMProviderConfig, LLMProviderType
    from .agentic_runner import test_llm_connection as do_test

    # Map provider string to enum
    provider_map = {
        "ollama": LLMProviderType.OLLAMA,
        "lm_studio": LLMProviderType.LM_STUDIO,
        "openrouter": LLMProviderType.OPENROUTER,
    }

    if request.provider not in provider_map:
        raise HTTPException(status_code=400, detail=f"Invalid provider: {request.provider}. Must be one of: ollama, lm_studio, openrouter")

    provider_type = provider_map[request.provider]

    # Validate OpenRouter requires API key
    if provider_type == LLMProviderType.OPENROUTER and not request.api_key:
        raise HTTPException(status_code=400, detail="OpenRouter requires an API key")

    config = LLMProviderConfig(
        provider_type=provider_type,
        model_name=request.model_name,
        api_url=request.api_url,
        api_key=request.api_key,
    )

    result = await do_test(config)
    return result


@app.get("/api/llm/ollama/models")
async def list_ollama_models(api_url: str = "http://localhost:11434"):
    """List available models from an Ollama instance"""
    try:
        import httpx
    except ImportError:
        raise HTTPException(status_code=500, detail="httpx not installed. Run: pip install httpx")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{api_url}/api/tags")
            response.raise_for_status()
            data = response.json()

            models = []
            for model in data.get("models", []):
                models.append({
                    "name": model.get("name"),
                    "size": model.get("size"),
                    "modified_at": model.get("modified_at"),
                    "digest": model.get("digest", "")[:12],  # Short digest
                })

            return {
                "success": True,
                "api_url": api_url,
                "models": models,
                "count": len(models)
            }

    except httpx.ConnectError:
        return {
            "success": False,
            "api_url": api_url,
            "error": f"Cannot connect to Ollama at {api_url}. Is Ollama running?",
            "hint": "Start Ollama with: ollama serve"
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "api_url": api_url,
            "error": f"HTTP error: {e.response.status_code}"
        }
    except Exception as e:
        return {
            "success": False,
            "api_url": api_url,
            "error": str(e)
        }


@app.get("/api/llm/lmstudio/models")
async def list_lmstudio_models(api_url: str = "http://localhost:1234/v1"):
    """List available models from an LM Studio instance"""
    try:
        import httpx
    except ImportError:
        raise HTTPException(status_code=500, detail="httpx not installed. Run: pip install httpx")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{api_url}/models")
            response.raise_for_status()
            data = response.json()

            models = []
            for model in data.get("data", []):
                models.append({
                    "id": model.get("id"),
                    "object": model.get("object"),
                    "owned_by": model.get("owned_by", "local"),
                })

            return {
                "success": True,
                "api_url": api_url,
                "models": models,
                "count": len(models)
            }

    except httpx.ConnectError:
        return {
            "success": False,
            "api_url": api_url,
            "error": f"Cannot connect to LM Studio at {api_url}. Is LM Studio running with the server enabled?",
            "hint": "Enable 'Local Server' in LM Studio settings"
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "api_url": api_url,
            "error": f"HTTP error: {e.response.status_code}"
        }
    except Exception as e:
        return {
            "success": False,
            "api_url": api_url,
            "error": str(e)
        }


@app.get("/api/llm/openrouter/models")
async def list_openrouter_models(api_key: str = None):
    """List available models from OpenRouter"""
    try:
        import httpx
    except ImportError:
        raise HTTPException(status_code=500, detail="httpx not installed. Run: pip install httpx")

    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required for OpenRouter")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://autowrkers.local",
                }
            )
            response.raise_for_status()
            data = response.json()

            # Filter and format models
            models = []
            for model in data.get("data", []):
                model_id = model.get("id", "")
                # Include popular providers
                if any(p in model_id for p in ["anthropic", "openai", "meta-llama", "google", "mistral", "cohere"]):
                    models.append({
                        "id": model_id,
                        "name": model.get("name", model_id),
                        "context_length": model.get("context_length"),
                        "pricing": model.get("pricing", {}),
                    })

            # Sort by provider then name
            models.sort(key=lambda m: m["id"])

            return {
                "success": True,
                "models": models[:100],  # Limit to top 100
                "count": len(models)
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {
                "success": False,
                "error": "Invalid API key"
            }
        return {
            "success": False,
            "error": f"HTTP error: {e.response.status_code}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ==================== Settings Page ====================

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings and configuration page"""
    return templates.TemplateResponse("settings.html", {"request": request})


# ==================== Daemon Management Endpoints ====================

@app.get("/api/daemon/status")
async def get_daemon_status():
    """Get daemon status"""
    status = await daemon_manager.get_status()
    return status.to_dict()


@app.post("/api/daemon/install")
async def install_daemon():
    """Install Autowrkers as a system daemon"""
    result = await daemon_manager.install()
    return result


@app.post("/api/daemon/uninstall")
async def uninstall_daemon():
    """Uninstall the system daemon"""
    result = await daemon_manager.uninstall()
    return result


@app.post("/api/daemon/start")
async def start_daemon():
    """Start the daemon service"""
    result = await daemon_manager.start()
    return result


@app.post("/api/daemon/stop")
async def stop_daemon():
    """Stop the daemon service"""
    result = await daemon_manager.stop()
    return result


@app.post("/api/daemon/restart")
async def restart_daemon():
    """Restart the daemon service"""
    result = await daemon_manager.restart()
    return result


@app.get("/api/daemon/logs")
async def get_daemon_logs(lines: int = 100):
    """Get daemon logs"""
    logs = daemon_manager.get_logs(lines)
    return {"logs": logs}


# ==================== Scheduler Endpoints ====================

@app.get("/api/scheduler/status")
async def get_scheduler_status():
    """Get scheduler status and all tasks"""
    return task_scheduler.get_status()


@app.get("/api/scheduler/tasks")
async def get_scheduler_tasks():
    """Get all scheduled tasks"""
    return {"tasks": [t.to_dict() for t in task_scheduler.get_all_tasks()]}


@app.get("/api/scheduler/tasks/{task_id}")
async def get_scheduler_task(task_id: str):
    """Get a specific scheduled task"""
    task = task_scheduler.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task.to_dict()}


class ScheduledTaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    task_type: str = Field(..., max_length=50)
    schedule: str = Field(..., max_length=100)
    enabled: bool = True
    project_id: Optional[int] = None
    config: Optional[dict] = None


@app.post("/api/scheduler/tasks")
async def create_scheduler_task(task_data: ScheduledTaskCreate):
    """Create a new scheduled task"""
    import uuid

    # Map task type string to enum
    type_map = {
        "issue_sync": TaskType.ISSUE_SYNC,
        "health_check": TaskType.HEALTH_CHECK,
        "session_cleanup": TaskType.SESSION_CLEANUP,
        "auto_retry": TaskType.AUTO_RETRY,
        "pr_status_check": TaskType.PR_STATUS_CHECK,
        "custom": TaskType.CUSTOM,
    }

    if task_data.task_type not in type_map:
        raise HTTPException(status_code=400, detail=f"Invalid task type: {task_data.task_type}")

    task = ScheduledTask(
        id=f"custom_{uuid.uuid4().hex[:8]}",
        name=task_data.name,
        task_type=type_map[task_data.task_type],
        schedule=task_data.schedule,
        enabled=task_data.enabled,
        project_id=task_data.project_id,
        config=task_data.config or {},
    )

    task_scheduler.add_task(task)
    return {"success": True, "task": task.to_dict()}


@app.put("/api/scheduler/tasks/{task_id}/enable")
async def enable_scheduler_task(task_id: str):
    """Enable a scheduled task"""
    if not task_scheduler.enable_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    task = task_scheduler.get_task(task_id)
    return {"success": True, "task": task.to_dict() if task else None}


@app.put("/api/scheduler/tasks/{task_id}/disable")
async def disable_scheduler_task(task_id: str):
    """Disable a scheduled task"""
    if not task_scheduler.disable_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    task = task_scheduler.get_task(task_id)
    return {"success": True, "task": task.to_dict() if task else None}


@app.post("/api/scheduler/tasks/{task_id}/run")
async def run_scheduler_task_now(task_id: str):
    """Run a scheduled task immediately"""
    if not task_scheduler.run_task_now(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "message": "Task triggered"}


@app.delete("/api/scheduler/tasks/{task_id}")
async def delete_scheduler_task(task_id: str):
    """Delete a scheduled task"""
    # Don't allow deleting global tasks
    if task_id.startswith("global_"):
        raise HTTPException(status_code=400, detail="Cannot delete built-in global tasks")
    if not task_scheduler.remove_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}


# ==================== Webhook Endpoints ====================

@app.get("/api/webhooks/status")
async def get_webhooks_status():
    """Get webhook handler status"""
    return webhook_handler.get_status()


@app.get("/api/webhooks/events")
async def get_webhook_events(limit: int = 100):
    """Get recent webhook events"""
    return {"events": webhook_handler.get_event_log(limit)}


@app.get("/api/projects/{project_id}/webhooks")
async def get_project_webhook_config(project_id: int):
    """Get webhook configuration for a project"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    config = webhook_handler.get_config(project_id)
    if not config:
        # Return default config
        config = WebhookConfig(project_id=project_id)

    return {"config": config.to_dict()}


class WebhookConfigUpdate(BaseModel):
    enabled: bool = True
    github_secret: Optional[str] = Field(None, max_length=256)
    auto_queue_issues: bool = True
    auto_start_on_label: str = Field("", max_length=100)
    trigger_labels: List[str] = Field(default_factory=list, max_length=50)
    ignore_labels: List[str] = Field(default_factory=list, max_length=50)


@app.put("/api/projects/{project_id}/webhooks")
async def update_project_webhook_config(project_id: int, config_data: WebhookConfigUpdate):
    """Update webhook configuration for a project"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    config = WebhookConfig(
        project_id=project_id,
        enabled=config_data.enabled,
        github_secret=config_data.github_secret or "",
        auto_queue_issues=config_data.auto_queue_issues,
        auto_start_on_label=config_data.auto_start_on_label,
        trigger_labels=config_data.trigger_labels,
        ignore_labels=config_data.ignore_labels,
    )

    webhook_handler.set_config(config)
    return {"success": True, "config": config.to_dict()}


@app.get("/api/projects/{project_id}/webhooks/events")
async def get_project_webhook_events(project_id: int, limit: int = 50):
    """Get webhook events for a specific project"""
    project = project_manager.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    events = webhook_handler.get_events_by_project(project_id, limit)
    return {"events": events}


@app.post("/webhooks/github")
async def github_webhook_endpoint(request: Request):
    """Receive GitHub webhook events"""
    # Get headers
    event_type = request.headers.get("x-github-event", "")
    signature = request.headers.get("x-hub-signature-256", request.headers.get("x-hub-signature", ""))

    # Get raw payload for signature verification
    raw_payload = await request.body()

    # Parse JSON payload
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Process the webhook
    result = await webhook_handler.process_github_webhook(
        event_type=event_type,
        payload=payload,
        headers=dict(request.headers),
        raw_payload=raw_payload
    )

    if not result.get("success"):
        # Return 200 even for ignored events (GitHub expects 2xx)
        return result

    return result


@app.post("/webhooks/custom/{path:path}")
async def custom_webhook_endpoint(path: str, request: Request):
    """Receive custom webhook events"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    result = await webhook_handler.process_custom_webhook(
        path=path,
        payload=payload,
        headers=dict(request.headers)
    )

    return result


# ==================== Notification Endpoints ====================

@app.get("/api/notifications/status")
async def get_notifications_status():
    """Get notification system status"""
    return notification_manager.get_status()


@app.get("/api/notifications/configs")
async def get_notification_configs():
    """Get all notification configurations"""
    configs = notification_manager.get_all_configs()
    return {"configs": [c.to_dict() for c in configs]}


@app.get("/api/notifications/configs/{config_id}")
async def get_notification_config(config_id: str):
    """Get a specific notification configuration"""
    config = notification_manager.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")
    return {"config": config.to_dict()}


class NotificationConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    channel: str = Field(..., max_length=50)
    enabled: bool = True
    project_id: Optional[int] = None  # None = global
    events: List[str] = Field(default_factory=list, max_length=50)
    # Channel-specific settings
    webhook_url: Optional[str] = Field(None, max_length=2000)
    # Telegram settings
    bot_token: Optional[str] = Field(None, max_length=200)
    chat_id: Optional[str] = Field(None, max_length=100)
    # Email settings
    smtp_host: Optional[str] = Field(None, max_length=500)
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_user: Optional[str] = Field(None, max_length=500)
    smtp_password: Optional[str] = Field(None, max_length=500)
    smtp_from: Optional[str] = Field(None, max_length=500)
    smtp_to: Optional[List[str]] = Field(None, max_length=50)
    smtp_use_tls: bool = True


@app.post("/api/notifications/configs")
async def create_notification_config(config_data: NotificationConfigCreate):
    """Create a new notification configuration"""
    import uuid

    # Map channel string to enum
    channel_map = {
        "discord": NotificationChannel.DISCORD,
        "slack": NotificationChannel.SLACK,
        "telegram": NotificationChannel.TELEGRAM,
        "email": NotificationChannel.EMAIL,
        "desktop": NotificationChannel.DESKTOP,
    }

    if config_data.channel not in channel_map:
        raise HTTPException(status_code=400, detail=f"Invalid channel: {config_data.channel}")

    # Map event strings to enum
    event_map = {
        "issue.started": NotificationEvent.ISSUE_STARTED,
        "issue.completed": NotificationEvent.ISSUE_COMPLETED,
        "issue.failed": NotificationEvent.ISSUE_FAILED,
        "issue.needs_review": NotificationEvent.ISSUE_NEEDS_REVIEW,
        "issue.queued": NotificationEvent.ISSUE_QUEUED,
        "pr.created": NotificationEvent.PR_CREATED,
        "pr.merged": NotificationEvent.PR_MERGED,
        "session.error": NotificationEvent.SESSION_ERROR,
        "session.needs_attention": NotificationEvent.SESSION_NEEDS_ATTENTION,
        "system.update_available": NotificationEvent.SYSTEM_UPDATE_AVAILABLE,
        "system.health_warning": NotificationEvent.SYSTEM_HEALTH_WARNING,
    }

    events = []
    for event_str in config_data.events:
        if event_str in event_map:
            events.append(event_map[event_str])

    # Build settings dict based on channel
    settings = {}
    if config_data.channel in ("discord", "slack"):
        settings["webhook_url"] = config_data.webhook_url or ""
    elif config_data.channel == "telegram":
        settings["bot_token"] = config_data.bot_token or ""
        settings["chat_id"] = config_data.chat_id or ""
    elif config_data.channel == "email":
        settings["smtp_host"] = config_data.smtp_host or ""
        settings["smtp_port"] = config_data.smtp_port
        settings["smtp_user"] = config_data.smtp_user or ""
        settings["smtp_password"] = config_data.smtp_password or ""
        settings["smtp_from"] = config_data.smtp_from or ""
        settings["smtp_to"] = config_data.smtp_to or []
        settings["smtp_use_tls"] = config_data.smtp_use_tls

    config = ChannelConfig(
        id=f"notif_{uuid.uuid4().hex[:8]}",
        name=config_data.name,
        channel=channel_map[config_data.channel],
        enabled=config_data.enabled,
        project_id=config_data.project_id,
        events=[e.value for e in events],
        settings=settings,
    )

    notification_manager.add_config(config)
    return {"success": True, "config": config.to_dict()}


@app.put("/api/notifications/configs/{config_id}")
async def update_notification_config(config_id: str, config_data: NotificationConfigCreate):
    """Update a notification configuration"""
    existing = notification_manager.get_config(config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Configuration not found")

    # Map channel string to enum
    channel_map = {
        "discord": NotificationChannel.DISCORD,
        "slack": NotificationChannel.SLACK,
        "telegram": NotificationChannel.TELEGRAM,
        "email": NotificationChannel.EMAIL,
        "desktop": NotificationChannel.DESKTOP,
    }

    # Map event strings to enum
    event_map = {
        "issue.started": NotificationEvent.ISSUE_STARTED,
        "issue.completed": NotificationEvent.ISSUE_COMPLETED,
        "issue.failed": NotificationEvent.ISSUE_FAILED,
        "issue.needs_review": NotificationEvent.ISSUE_NEEDS_REVIEW,
        "issue.queued": NotificationEvent.ISSUE_QUEUED,
        "pr.created": NotificationEvent.PR_CREATED,
        "pr.merged": NotificationEvent.PR_MERGED,
        "session.error": NotificationEvent.SESSION_ERROR,
        "session.needs_attention": NotificationEvent.SESSION_NEEDS_ATTENTION,
        "system.update_available": NotificationEvent.SYSTEM_UPDATE_AVAILABLE,
        "system.health_warning": NotificationEvent.SYSTEM_HEALTH_WARNING,
    }

    events = []
    for event_str in config_data.events:
        if event_str in event_map:
            events.append(event_map[event_str])

    # Build settings dict based on channel
    settings = {}
    if config_data.channel in ("discord", "slack"):
        settings["webhook_url"] = config_data.webhook_url or ""
    elif config_data.channel == "telegram":
        settings["bot_token"] = config_data.bot_token or ""
        settings["chat_id"] = config_data.chat_id or ""
    elif config_data.channel == "email":
        settings["smtp_host"] = config_data.smtp_host or ""
        settings["smtp_port"] = config_data.smtp_port
        settings["smtp_user"] = config_data.smtp_user or ""
        settings["smtp_password"] = config_data.smtp_password or ""
        settings["smtp_from"] = config_data.smtp_from or ""
        settings["smtp_to"] = config_data.smtp_to or []
        settings["smtp_use_tls"] = config_data.smtp_use_tls

    # Update the config
    existing.name = config_data.name
    existing.channel = channel_map[config_data.channel]
    existing.enabled = config_data.enabled
    existing.project_id = config_data.project_id
    existing.events = [e.value for e in events]
    existing.settings = settings

    # Re-sync to legacy config
    notification_manager._sync_to_legacy_config(existing)

    return {"success": True, "config": existing.to_dict()}


@app.delete("/api/notifications/configs/{config_id}")
async def delete_notification_config(config_id: str):
    """Delete a notification configuration"""
    if not notification_manager.remove_config(config_id):
        raise HTTPException(status_code=404, detail="Configuration not found")
    return {"success": True}


@app.post("/api/notifications/configs/{config_id}/test")
async def test_notification_config(config_id: str):
    """Send a test notification"""
    config = notification_manager.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    success = await notification_manager.send_test(config)
    return {"success": success}


@app.get("/api/notifications/log")
async def get_notification_log(limit: int = 100):
    """Get notification log"""
    return {"notifications": notification_manager.get_notification_log(limit)}


# ==================== System Settings Endpoints ====================

@app.get("/api/settings")
async def get_system_settings():
    """Get all system settings"""
    from .database import db

    # Filter out sensitive internal settings
    HIDDEN_SETTINGS = {'jwt_secret', 'auth_users', 'auth_enabled'}
    settings = {
        k: v for k, v in db.get_all_settings().items()
        if k not in HIDDEN_SETTINGS
    }
    return {"settings": settings}


# Sensitive settings that cannot be read or written via the API
PROTECTED_SETTINGS = {'jwt_secret', 'auth_users', 'auth_enabled'}


@app.get("/api/settings/{key}")
async def get_system_setting(key: str):
    """Get a specific system setting"""
    from .database import db

    if key in PROTECTED_SETTINGS:
        raise HTTPException(status_code=403, detail="Access denied to protected setting")

    value = db.get_setting(key)
    if value is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    return {"key": key, "value": value}


class SettingUpdate(BaseModel):
    value: str = Field(..., max_length=10000)


@app.put("/api/settings/{key}")
async def update_system_setting(key: str, setting: SettingUpdate):
    """Update a system setting"""
    from .database import db

    if key in PROTECTED_SETTINGS:
        raise HTTPException(status_code=403, detail="Cannot modify protected setting")

    db.set_setting(key, setting.value)
    return {"success": True, "key": key, "value": setting.value}


# ==================== Telegram Bot API ====================

class TelegramStartRequest(BaseModel):
    bot_token: Optional[str] = None
    allowed_user_ids: List[int] = []

class TelegramConfigUpdate(BaseModel):
    bot_token: Optional[str] = None
    allowed_user_ids: Optional[List[int]] = None
    push_session_status: Optional[bool] = None
    push_automation_events: Optional[bool] = None
    push_session_output: Optional[bool] = None
    output_max_lines: Optional[int] = None


@app.get("/api/telegram/status")
async def telegram_status():
    """Get Telegram bot status."""
    return telegram_bot.get_status()


@app.post("/api/telegram/start")
async def telegram_start(req: TelegramStartRequest):
    """Start the Telegram bot."""
    try:
        await telegram_bot.start(token=req.bot_token, allowed_users=req.allowed_user_ids)
        # Register callbacks only after successful start
        if telegram_bot._running:
            manager.add_status_callback(telegram_bot._on_session_status)
            manager.add_completion_callback(telegram_bot._on_session_complete)
            automation_controller.add_event_callback(telegram_bot._on_automation_event)
        return {"success": telegram_bot._running, "status": telegram_bot.get_status()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/telegram/stop")
async def telegram_stop():
    """Stop the Telegram bot."""
    try:
        await telegram_bot.stop()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/telegram/config")
async def telegram_get_config():
    """Get Telegram bot config (token masked)."""
    return telegram_bot.get_config().to_safe_dict()


@app.put("/api/telegram/config")
async def telegram_update_config(req: TelegramConfigUpdate):
    """Update Telegram bot config."""
    update_data = req.dict(exclude_none=True)
    telegram_bot.update_config(update_data)
    return {"success": True, "config": telegram_bot.get_config().to_safe_dict()}


def run_server(
    host: str = "127.0.0.1",
    port: int = 8420,
    ssl_certfile: Optional[str] = None,
    ssl_keyfile: Optional[str] = None,
):
    """
    Run the Autowrkers server.

    Args:
        host: Bind address. Default is 127.0.0.1 (localhost only) for security.
              Use 0.0.0.0 to allow external connections, but ensure authentication
              is enabled and a firewall is configured.
        port: Port to listen on (default: 8420)
        ssl_certfile: Path to SSL certificate file (PEM format).
                      Can also be set via AUTOWRKERS_SSL_CERTFILE env var.
        ssl_keyfile: Path to SSL private key file (PEM format).
                     Can also be set via AUTOWRKERS_SSL_KEYFILE env var.
    """
    import uvicorn

    # Warn if binding to all interfaces without auth
    if host == "0.0.0.0" and not is_auth_enabled():
        logger.warning(
            "WARNING: Server is binding to 0.0.0.0 but authentication is DISABLED. "
            "This exposes Autowrkers to the network without protection. "
            "Enable auth with AUTOWRKERS_AUTH_ENABLED=true or restrict to localhost."
        )

    # SSL configuration from args or env vars
    certfile = ssl_certfile or os.environ.get("AUTOWRKERS_SSL_CERTFILE")
    keyfile = ssl_keyfile or os.environ.get("AUTOWRKERS_SSL_KEYFILE")

    kwargs = {"host": host, "port": port}

    if certfile and keyfile:
        if not os.path.isfile(certfile):
            logger.error(f"SSL certificate file not found: {certfile}")
            return
        if not os.path.isfile(keyfile):
            logger.error(f"SSL key file not found: {keyfile}")
            return
        kwargs["ssl_certfile"] = certfile
        kwargs["ssl_keyfile"] = keyfile
        logger.info(f"HTTPS enabled with cert={certfile}")
    elif certfile or keyfile:
        logger.error("Both ssl_certfile and ssl_keyfile must be provided for HTTPS")
        return

    uvicorn.run(app, **kwargs)
