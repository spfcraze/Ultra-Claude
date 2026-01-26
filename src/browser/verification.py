"""
Browser-based verification for workflow phases.

Provides a verification step that launches a browser, navigates to URLs,
takes screenshots, and runs assertions (element exists, text contains, JS eval).
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from ..logging_config import get_logger
from .manager import browser_manager
from .models import BrowserSessionConfig

logger = get_logger("ultraclaude.browser.verification")


@dataclass
class VerificationCheck:
    """A single browser verification check."""
    name: str
    url: str
    checks: List[Dict[str, Any]] = field(default_factory=list)
    # Each check: {"type": "selector_exists"|"text_contains"|"js_eval", "value": "...", "expected": ...}
    take_screenshot: bool = True


@dataclass
class VerificationResult:
    """Result of browser verification."""
    passed: bool
    checks_passed: int = 0
    checks_failed: int = 0
    screenshots: List[str] = field(default_factory=list)
    details: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "screenshots": self.screenshots,
            "details": self.details,
            "error": self.error,
        }


async def run_browser_verification(
    checks: List[VerificationCheck],
    config: Optional[BrowserSessionConfig] = None,
) -> VerificationResult:
    """
    Run a series of browser verification checks.

    Creates a temporary browser session, runs all checks, then closes.
    Returns a VerificationResult with pass/fail status and screenshots.
    """
    result = VerificationResult(passed=True)

    session = await browser_manager.create_session(
        name="Workflow Verification",
        config=config or BrowserSessionConfig(headless=True),
    )

    try:
        for check in checks:
            nav_action = await browser_manager.navigate(session.id, check.url)
            if nav_action.error:
                result.details.append({
                    "name": check.name,
                    "passed": False,
                    "error": f"Navigation failed: {nav_action.error}",
                })
                result.checks_failed += 1
                result.passed = False
                continue

            if check.take_screenshot:
                screenshot = await browser_manager.screenshot(session.id)
                result.screenshots.append(screenshot.to_dict()["serve_url"])

            for assertion in check.checks:
                check_passed = False
                detail: Dict[str, Any] = {"name": check.name, "assertion": assertion}

                try:
                    if assertion["type"] == "selector_exists":
                        action = await browser_manager.wait_for_selector(
                            session.id, assertion["value"], timeout_ms=5000
                        )
                        check_passed = action.error is None

                    elif assertion["type"] == "text_contains":
                        action = await browser_manager.extract_text(session.id)
                        if action.result and assertion.get("expected", "") in str(action.result):
                            check_passed = True

                    elif assertion["type"] == "js_eval":
                        action = await browser_manager.evaluate(session.id, assertion["value"])
                        check_passed = action.result == assertion.get("expected", True)

                except Exception as e:
                    detail["error"] = str(e)

                detail["passed"] = check_passed
                result.details.append(detail)

                if check_passed:
                    result.checks_passed += 1
                else:
                    result.checks_failed += 1
                    result.passed = False

    except Exception as e:
        result.passed = False
        result.error = str(e)
        logger.error(f"Browser verification error: {e}")
    finally:
        await browser_manager.close_session(session.id)

    logger.info(
        f"Browser verification: {'PASSED' if result.passed else 'FAILED'} "
        f"({result.checks_passed}/{result.checks_passed + result.checks_failed} checks passed)"
    )
    return result
