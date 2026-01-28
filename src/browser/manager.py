"""
BrowserManager - Manages Playwright browser sessions.

Provides a singleton manager for creating and controlling browser sessions
with screenshot capture, navigation, interaction, and log collection.
"""
import asyncio
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Callable, Awaitable, Any, List

from ..logging_config import get_logger

from .models import (
    BrowserSession,
    BrowserSessionConfig,
    BrowserSessionStatus,
    BrowserType,
    ActionType,
    BrowserAction,
    ConsoleLogEntry,
    NetworkLogEntry,
    ScreenshotRecord,
)

logger = get_logger("autowrkers.browser")

# Screenshots go into web/static/screenshots/ for direct serving
BASE_DIR = Path(__file__).parent.parent.parent
SCREENSHOTS_DIR = BASE_DIR / "web" / "static" / "screenshots"


class BrowserManager:
    """Manages multiple concurrent Playwright browser sessions."""

    def __init__(self):
        self.sessions: Dict[str, BrowserSession] = {}
        self._playwright = None
        self._browsers: Dict[str, Any] = {}
        self._contexts: Dict[str, Any] = {}
        self._pages: Dict[str, Any] = {}
        self._status_callbacks: List[Callable] = []
        self._action_callbacks: List[Callable] = []
        self._initialized = False

        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    async def _ensure_playwright(self):
        """Lazy-init Playwright on first use."""
        if not self._initialized:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._initialized = True
            logger.info("Playwright initialized")

    def add_status_callback(self, callback: Callable):
        self._status_callbacks.append(callback)

    def add_action_callback(self, callback: Callable):
        self._action_callbacks.append(callback)

    async def _notify_status(self, session_id: str, status: BrowserSessionStatus):
        for callback in self._status_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(session_id, status)
                else:
                    callback(session_id, status)
            except Exception as e:
                logger.error(f"Browser status callback error: {e}")

    async def _notify_action(self, session_id: str, action: BrowserAction):
        for callback in self._action_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(session_id, action)
                else:
                    callback(session_id, action)
            except Exception as e:
                logger.error(f"Browser action callback error: {e}")

    # ==================== Session Lifecycle ====================

    async def create_session(
        self,
        name: Optional[str] = None,
        config: Optional[BrowserSessionConfig] = None,
    ) -> BrowserSession:
        """Create and launch a new browser session."""
        await self._ensure_playwright()

        session_id = uuid.uuid4().hex[:8]
        if name is None:
            name = f"Browser {session_id}"
        if config is None:
            config = BrowserSessionConfig()

        session = BrowserSession(id=session_id, name=name, config=config)
        self.sessions[session_id] = session

        try:
            launcher = {
                BrowserType.CHROMIUM: self._playwright.chromium,
                BrowserType.FIREFOX: self._playwright.firefox,
                BrowserType.WEBKIT: self._playwright.webkit,
            }[config.browser_type]

            browser = await launcher.launch(headless=config.headless)
            self._browsers[session_id] = browser

            context_options = {
                "viewport": {
                    "width": config.viewport_width,
                    "height": config.viewport_height,
                },
                "device_scale_factor": config.device_scale_factor,
                "ignore_https_errors": config.ignore_https_errors,
            }
            if config.user_agent:
                context_options["user_agent"] = config.user_agent

            context = await browser.new_context(**context_options)
            self._contexts[session_id] = context

            page = await context.new_page()
            page.set_default_timeout(config.timeout_ms)
            self._pages[session_id] = page

            if config.record_console:
                page.on("console", lambda msg: self._on_console(session_id, msg))

            if config.record_network:
                page.on("response", lambda res: self._on_response(session_id, res))
                page.on("requestfailed", lambda req: self._on_request_failed(session_id, req))

            session.status = BrowserSessionStatus.IDLE
            await self._notify_status(session_id, session.status)
            logger.info(f"Created browser session {session_id}: {name}")
            return session

        except Exception as e:
            session.status = BrowserSessionStatus.ERROR
            await self._notify_status(session_id, session.status)
            logger.error(f"Failed to create browser session: {e}")
            raise

    async def close_session(self, session_id: str) -> bool:
        """Close a browser session and clean up resources."""
        session = self.sessions.get(session_id)
        if not session:
            return False

        try:
            if session_id in self._contexts:
                await self._contexts[session_id].close()
            if session_id in self._browsers:
                await self._browsers[session_id].close()
        except Exception as e:
            logger.error(f"Error closing browser session {session_id}: {e}")

        self._pages.pop(session_id, None)
        self._contexts.pop(session_id, None)
        self._browsers.pop(session_id, None)

        session.status = BrowserSessionStatus.CLOSED
        await self._notify_status(session_id, session.status)
        logger.info(f"Closed browser session {session_id}")
        return True

    async def close_all(self):
        """Close all browser sessions. Called on server shutdown."""
        for session_id in list(self.sessions.keys()):
            await self.close_session(session_id)
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
            self._initialized = False
            logger.info("Playwright stopped")

    def get_session(self, session_id: str) -> Optional[BrowserSession]:
        return self.sessions.get(session_id)

    def get_all_sessions(self) -> List[BrowserSession]:
        return list(self.sessions.values())

    # ==================== Browser Actions ====================

    async def navigate(self, session_id: str, url: str, wait_until: str = "load") -> BrowserAction:
        """Navigate to a URL."""
        page = self._get_page(session_id)
        session = self.sessions[session_id]
        action = BrowserAction(action_type=ActionType.NAVIGATE, params={"url": url, "wait_until": wait_until})
        start = time.monotonic()

        try:
            session.status = BrowserSessionStatus.NAVIGATING
            await self._notify_status(session_id, session.status)

            response = await page.goto(url, wait_until=wait_until)
            action.result = {
                "status": response.status if response else None,
                "url": page.url,
                "title": await page.title(),
            }
            session.current_url = page.url
            session.page_title = await page.title()
            session.status = BrowserSessionStatus.IDLE
        except Exception as e:
            action.error = str(e)
            session.status = BrowserSessionStatus.ERROR

        action.duration_ms = (time.monotonic() - start) * 1000
        session.add_action(action)
        await self._notify_status(session_id, session.status)
        await self._notify_action(session_id, action)
        return action

    async def screenshot(
        self,
        session_id: str,
        full_page: bool = False,
        selector: Optional[str] = None,
    ) -> ScreenshotRecord:
        """Take a screenshot of the current page or a specific element."""
        page = self._get_page(session_id)
        session = self.sessions[session_id]

        screenshot_id = uuid.uuid4().hex[:8]
        filename = f"browser-{session_id}-{screenshot_id}.png"
        file_path = SCREENSHOTS_DIR / filename

        if selector:
            element = page.locator(selector)
            await element.screenshot(path=str(file_path))
        else:
            await page.screenshot(path=str(file_path), full_page=full_page)

        record = ScreenshotRecord(
            id=screenshot_id,
            session_id=session_id,
            filename=filename,
            url=session.current_url,
            width=session.config.viewport_width,
            height=session.config.viewport_height,
            full_page=full_page,
            selector=selector or "",
            file_path=str(file_path),
        )
        session.screenshots.append(record)

        action = BrowserAction(
            action_type=ActionType.SCREENSHOT,
            params={"full_page": full_page, "selector": selector},
            result=record.to_dict(),
        )
        session.add_action(action)
        await self._notify_action(session_id, action)
        return record

    async def click(self, session_id: str, selector: str, **kwargs) -> BrowserAction:
        """Click an element."""
        page = self._get_page(session_id)
        session = self.sessions[session_id]
        action = BrowserAction(action_type=ActionType.CLICK, params={"selector": selector})
        start = time.monotonic()

        try:
            await page.click(selector, **kwargs)
            action.result = {"success": True}
            session.current_url = page.url
            session.page_title = await page.title()
        except Exception as e:
            action.error = str(e)

        action.duration_ms = (time.monotonic() - start) * 1000
        session.add_action(action)
        await self._notify_action(session_id, action)
        return action

    async def type_text(self, session_id: str, selector: str, text: str, **kwargs) -> BrowserAction:
        """Type text into an element."""
        page = self._get_page(session_id)
        action = BrowserAction(action_type=ActionType.TYPE, params={"selector": selector, "text": text})
        start = time.monotonic()

        try:
            await page.fill(selector, text, **kwargs)
            action.result = {"success": True}
        except Exception as e:
            action.error = str(e)

        action.duration_ms = (time.monotonic() - start) * 1000
        self.sessions[session_id].add_action(action)
        await self._notify_action(session_id, action)
        return action

    async def evaluate(self, session_id: str, expression: str) -> BrowserAction:
        """Evaluate JavaScript in the page context."""
        page = self._get_page(session_id)
        action = BrowserAction(action_type=ActionType.EVALUATE, params={"expression": expression})
        start = time.monotonic()

        try:
            result = await page.evaluate(expression)
            action.result = result
        except Exception as e:
            action.error = str(e)

        action.duration_ms = (time.monotonic() - start) * 1000
        self.sessions[session_id].add_action(action)
        await self._notify_action(session_id, action)
        return action

    async def extract_text(self, session_id: str, selector: Optional[str] = None) -> BrowserAction:
        """Extract text content from the page or a specific element."""
        page = self._get_page(session_id)
        action = BrowserAction(action_type=ActionType.EXTRACT_TEXT, params={"selector": selector})
        start = time.monotonic()

        try:
            if selector:
                element = page.locator(selector)
                text = await element.text_content()
            else:
                text = await page.evaluate("document.body.innerText")
            action.result = text
        except Exception as e:
            action.error = str(e)

        action.duration_ms = (time.monotonic() - start) * 1000
        self.sessions[session_id].add_action(action)
        await self._notify_action(session_id, action)
        return action

    async def extract_html(self, session_id: str, selector: Optional[str] = None) -> BrowserAction:
        """Extract HTML from the page or a specific element."""
        page = self._get_page(session_id)
        action = BrowserAction(action_type=ActionType.EXTRACT_HTML, params={"selector": selector})
        start = time.monotonic()

        try:
            if selector:
                element = page.locator(selector)
                html = await element.inner_html()
            else:
                html = await page.content()
            action.result = html
        except Exception as e:
            action.error = str(e)

        action.duration_ms = (time.monotonic() - start) * 1000
        self.sessions[session_id].add_action(action)
        await self._notify_action(session_id, action)
        return action

    async def wait_for_selector(
        self, session_id: str, selector: str, state: str = "visible", timeout_ms: int = 30000
    ) -> BrowserAction:
        """Wait for an element to appear."""
        page = self._get_page(session_id)
        action = BrowserAction(action_type=ActionType.WAIT, params={"selector": selector, "state": state})
        start = time.monotonic()

        try:
            await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
            action.result = {"found": True}
        except Exception as e:
            action.error = str(e)

        action.duration_ms = (time.monotonic() - start) * 1000
        self.sessions[session_id].add_action(action)
        await self._notify_action(session_id, action)
        return action

    # ==================== Log Access ====================

    def get_console_logs(self, session_id: str, level: Optional[str] = None, limit: int = 100) -> List[dict]:
        session = self.sessions.get(session_id)
        if not session:
            return []
        logs = session.console_logs
        if level:
            logs = [entry for entry in logs if entry.level == level]
        return [entry.to_dict() for entry in logs[-limit:]]

    def get_network_logs(self, session_id: str, limit: int = 100) -> List[dict]:
        session = self.sessions.get(session_id)
        if not session:
            return []
        return [entry.to_dict() for entry in session.network_logs[-limit:]]

    def get_screenshots(self, session_id: str) -> List[dict]:
        session = self.sessions.get(session_id)
        if not session:
            return []
        return [s.to_dict() for s in session.screenshots]

    def get_action_history(self, session_id: str, limit: int = 50) -> List[dict]:
        session = self.sessions.get(session_id)
        if not session:
            return []
        return [a.to_dict() for a in session.action_history[-limit:]]

    # ==================== Internal Helpers ====================

    def _get_page(self, session_id: str):
        """Get the Playwright page for a session."""
        page = self._pages.get(session_id)
        if not page:
            raise ValueError(f"Browser session not found or closed: {session_id}")
        return page

    def _on_console(self, session_id: str, msg):
        """Handle console message from page."""
        session = self.sessions.get(session_id)
        if session:
            entry = ConsoleLogEntry(
                level=msg.type,
                text=msg.text,
            )
            session.add_console_log(entry)

    def _on_response(self, session_id: str, response):
        """Handle network response."""
        session = self.sessions.get(session_id)
        if session:
            entry = NetworkLogEntry(
                method=response.request.method,
                url=response.url,
                status=response.status,
                resource_type=response.request.resource_type,
            )
            session.add_network_log(entry)

    def _on_request_failed(self, session_id: str, request):
        """Handle failed network request."""
        session = self.sessions.get(session_id)
        if session:
            failure = ""
            if hasattr(request, "failure") and request.failure:
                failure = request.failure
            entry = NetworkLogEntry(
                method=request.method,
                url=request.url,
                resource_type=request.resource_type,
                failed=True,
                failure_text=failure,
            )
            session.add_network_log(entry)


# Global singleton instance
browser_manager = BrowserManager()
