"""
Browser automation API endpoints.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .manager import browser_manager
from .models import BrowserSessionConfig, BrowserType

router = APIRouter(prefix="/api/browser", tags=["browser"])


# ==================== Request Models ====================

class BrowserSessionCreate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    headless: bool = True
    browser_type: str = Field("chromium", max_length=20)
    viewport_width: int = Field(1280, ge=320, le=3840)
    viewport_height: int = Field(720, ge=240, le=2160)
    device_scale_factor: float = Field(1.0, ge=0.5, le=3.0)
    user_agent: str = Field("", max_length=500)
    ignore_https_errors: bool = False
    timeout_ms: int = Field(30000, ge=1000, le=120000)
    record_console: bool = True
    record_network: bool = True


class NavigateRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)
    wait_until: str = Field("load", max_length=20)


class ClickRequest(BaseModel):
    selector: str = Field(..., min_length=1, max_length=500)


class TypeRequest(BaseModel):
    selector: str = Field(..., min_length=1, max_length=500)
    text: str = Field(..., max_length=10000)


class EvaluateRequest(BaseModel):
    expression: str = Field(..., min_length=1, max_length=50000)


class ScreenshotRequest(BaseModel):
    full_page: bool = False
    selector: Optional[str] = Field(None, max_length=500)


class ExtractRequest(BaseModel):
    selector: Optional[str] = Field(None, max_length=500)


class WaitRequest(BaseModel):
    selector: str = Field(..., min_length=1, max_length=500)
    state: str = Field("visible", max_length=20)
    timeout_ms: int = Field(30000, ge=1000, le=120000)


# ==================== Session Endpoints ====================

@router.get("/sessions")
async def list_browser_sessions():
    """List all browser sessions."""
    sessions = browser_manager.get_all_sessions()
    return {"sessions": [s.to_dict() for s in sessions]}


@router.post("/sessions")
async def create_browser_session(request: BrowserSessionCreate):
    """Create a new browser session."""
    try:
        browser_type = BrowserType(request.browser_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid browser type: {request.browser_type}")

    config = BrowserSessionConfig(
        headless=request.headless,
        browser_type=browser_type,
        viewport_width=request.viewport_width,
        viewport_height=request.viewport_height,
        device_scale_factor=request.device_scale_factor,
        user_agent=request.user_agent,
        ignore_https_errors=request.ignore_https_errors,
        timeout_ms=request.timeout_ms,
        record_console=request.record_console,
        record_network=request.record_network,
    )

    try:
        session = await browser_manager.create_session(name=request.name, config=config)
        return {"success": True, "session": session.to_dict()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}")
async def get_browser_session(session_id: str):
    """Get browser session details."""
    session = browser_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Browser session not found")
    return {"session": session.to_dict()}


@router.delete("/sessions/{session_id}")
async def close_browser_session(session_id: str):
    """Close and clean up a browser session."""
    if not await browser_manager.close_session(session_id):
        raise HTTPException(status_code=404, detail="Browser session not found")
    return {"success": True}


# ==================== Action Endpoints ====================

@router.post("/sessions/{session_id}/navigate")
async def navigate(session_id: str, request: NavigateRequest):
    """Navigate to a URL."""
    try:
        action = await browser_manager.navigate(session_id, request.url, request.wait_until)
        return {"success": action.error is None, "action": action.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/screenshot")
async def take_screenshot(session_id: str, request: ScreenshotRequest = ScreenshotRequest()):
    """Take a screenshot of the current page."""
    try:
        record = await browser_manager.screenshot(
            session_id,
            full_page=request.full_page,
            selector=request.selector,
        )
        return {"success": True, "screenshot": record.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/click")
async def click_element(session_id: str, request: ClickRequest):
    """Click an element on the page."""
    try:
        action = await browser_manager.click(session_id, request.selector)
        return {"success": action.error is None, "action": action.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/type")
async def type_text(session_id: str, request: TypeRequest):
    """Type text into an element."""
    try:
        action = await browser_manager.type_text(session_id, request.selector, request.text)
        return {"success": action.error is None, "action": action.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/evaluate")
async def evaluate_js(session_id: str, request: EvaluateRequest):
    """Evaluate JavaScript in the page context."""
    try:
        action = await browser_manager.evaluate(session_id, request.expression)
        return {"success": action.error is None, "action": action.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/extract/text")
async def extract_text(session_id: str, request: ExtractRequest = ExtractRequest()):
    """Extract text content from the page."""
    try:
        action = await browser_manager.extract_text(session_id, request.selector)
        return {"success": action.error is None, "action": action.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/extract/html")
async def extract_html(session_id: str, request: ExtractRequest = ExtractRequest()):
    """Extract HTML from the page."""
    try:
        action = await browser_manager.extract_html(session_id, request.selector)
        return {"success": action.error is None, "action": action.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/wait")
async def wait_for_element(session_id: str, request: WaitRequest):
    """Wait for an element to appear."""
    try:
        action = await browser_manager.wait_for_selector(
            session_id, request.selector, request.state, request.timeout_ms
        )
        return {"success": action.error is None, "action": action.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==================== Log Endpoints ====================

@router.get("/sessions/{session_id}/console")
async def get_console_logs(session_id: str, level: Optional[str] = None, limit: int = 100):
    """Get console logs for a browser session."""
    logs = browser_manager.get_console_logs(session_id, level=level, limit=limit)
    return {"logs": logs, "count": len(logs)}


@router.get("/sessions/{session_id}/network")
async def get_network_logs(session_id: str, limit: int = 100):
    """Get network logs for a browser session."""
    logs = browser_manager.get_network_logs(session_id, limit=limit)
    return {"logs": logs, "count": len(logs)}


@router.get("/sessions/{session_id}/screenshots")
async def get_screenshots(session_id: str):
    """Get all screenshots for a browser session."""
    screenshots = browser_manager.get_screenshots(session_id)
    return {"screenshots": screenshots, "count": len(screenshots)}


@router.get("/sessions/{session_id}/history")
async def get_action_history(session_id: str, limit: int = 50):
    """Get action history for a browser session."""
    history = browser_manager.get_action_history(session_id, limit=limit)
    return {"actions": history, "count": len(history)}


# ==================== Status Endpoint ====================

@router.get("/status")
async def browser_status():
    """Get overall browser automation status."""
    sessions = browser_manager.get_all_sessions()
    active = [s for s in sessions if s.status.value not in ("closed", "error")]
    return {
        "initialized": browser_manager._initialized,
        "total_sessions": len(sessions),
        "active_sessions": len(active),
        "sessions": [s.to_dict() for s in sessions],
    }
