"""
Browser automation data models.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class BrowserSessionStatus(Enum):
    STARTING = "starting"
    IDLE = "idle"
    NAVIGATING = "navigating"
    ERROR = "error"
    CLOSED = "closed"


class BrowserType(Enum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class ActionType(Enum):
    NAVIGATE = "navigate"
    SCREENSHOT = "screenshot"
    CLICK = "click"
    TYPE = "type"
    EVALUATE = "evaluate"
    EXTRACT_TEXT = "extract_text"
    EXTRACT_HTML = "extract_html"
    WAIT = "wait"
    SELECT = "select"
    SCROLL = "scroll"


@dataclass
class BrowserSessionConfig:
    """Configuration for a browser session."""
    headless: bool = True
    browser_type: BrowserType = BrowserType.CHROMIUM
    viewport_width: int = 1280
    viewport_height: int = 720
    device_scale_factor: float = 1.0
    user_agent: str = ""
    ignore_https_errors: bool = False
    timeout_ms: int = 30000
    record_console: bool = True
    record_network: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "headless": self.headless,
            "browser_type": self.browser_type.value,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
            "device_scale_factor": self.device_scale_factor,
            "user_agent": self.user_agent,
            "ignore_https_errors": self.ignore_https_errors,
            "timeout_ms": self.timeout_ms,
            "record_console": self.record_console,
            "record_network": self.record_network,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrowserSessionConfig":
        data = data.copy()
        if "browser_type" in data and isinstance(data["browser_type"], str):
            data["browser_type"] = BrowserType(data["browser_type"])
        return cls(**data)


@dataclass
class ConsoleLogEntry:
    """A browser console log entry."""
    level: str  # log, warn, error, info, debug
    text: str
    url: str = ""
    line_number: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "text": self.text,
            "url": self.url,
            "line_number": self.line_number,
            "timestamp": self.timestamp,
        }


@dataclass
class NetworkLogEntry:
    """A browser network request/response entry."""
    method: str
    url: str
    status: int = 0
    resource_type: str = ""
    response_size: int = 0
    duration_ms: float = 0.0
    failed: bool = False
    failure_text: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "url": self.url,
            "status": self.status,
            "resource_type": self.resource_type,
            "response_size": self.response_size,
            "duration_ms": self.duration_ms,
            "failed": self.failed,
            "failure_text": self.failure_text,
            "timestamp": self.timestamp,
        }


@dataclass
class ScreenshotRecord:
    """Record of a screenshot taken."""
    id: str
    session_id: str
    filename: str
    url: str
    width: int
    height: int
    full_page: bool = False
    selector: str = ""
    file_path: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "filename": self.filename,
            "url": self.url,
            "width": self.width,
            "height": self.height,
            "full_page": self.full_page,
            "selector": self.selector,
            "serve_url": f"/static/screenshots/{self.filename}",
            "created_at": self.created_at,
        }


@dataclass
class BrowserAction:
    """A queued or executed browser action."""
    action_type: ActionType
    params: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type.value,
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class BrowserSession:
    """A browser session with state tracking."""
    id: str
    name: str
    config: BrowserSessionConfig
    status: BrowserSessionStatus = BrowserSessionStatus.STARTING
    current_url: str = ""
    page_title: str = ""
    console_logs: List[ConsoleLogEntry] = field(default_factory=list)
    network_logs: List[NetworkLogEntry] = field(default_factory=list)
    screenshots: List[ScreenshotRecord] = field(default_factory=list)
    action_history: List[BrowserAction] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    _max_console_logs: int = 500
    _max_network_logs: int = 500
    _max_action_history: int = 200

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "config": self.config.to_dict(),
            "status": self.status.value,
            "current_url": self.current_url,
            "page_title": self.page_title,
            "console_log_count": len(self.console_logs),
            "network_log_count": len(self.network_logs),
            "screenshot_count": len(self.screenshots),
            "action_count": len(self.action_history),
            "created_at": self.created_at,
        }

    def add_console_log(self, entry: ConsoleLogEntry):
        self.console_logs.append(entry)
        if len(self.console_logs) > self._max_console_logs:
            self.console_logs = self.console_logs[-self._max_console_logs:]

    def add_network_log(self, entry: NetworkLogEntry):
        self.network_logs.append(entry)
        if len(self.network_logs) > self._max_network_logs:
            self.network_logs = self.network_logs[-self._max_network_logs:]

    def add_action(self, action: BrowserAction):
        self.action_history.append(action)
        if len(self.action_history) > self._max_action_history:
            self.action_history = self.action_history[-self._max_action_history:]
