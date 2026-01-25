#!/usr/bin/env python3
"""
Screenshot capture script for UltraClaude README
Uses Playwright to capture screenshots of the dashboard
"""

import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

SCREENSHOTS_DIR = Path(__file__).parent / "docs" / "screenshots"
BASE_URL = "http://localhost:8420"


async def take_screenshots():
    """Capture screenshots of UltraClaude dashboard"""

    # Ensure screenshots directory exists
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            device_scale_factor=2,  # Retina quality
        )
        page = await context.new_page()

        print("Capturing screenshots...")

        # 1. Sessions Dashboard (main page)
        print("  [1/6] Sessions Dashboard...")
        await page.goto(f"{BASE_URL}/")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(1)  # Let animations settle
        await page.screenshot(
            path=SCREENSHOTS_DIR / "sessions-dashboard.png",
            full_page=False
        )

        # 2. Kanban Board View
        print("  [2/6] Kanban Board...")
        # Click the kanban view button if it exists
        kanban_btn = page.locator('[data-view="kanban"]')
        if await kanban_btn.count() > 0:
            await kanban_btn.click()
            await asyncio.sleep(0.5)
        await page.screenshot(
            path=SCREENSHOTS_DIR / "kanban-board.png",
            full_page=False
        )

        # 3. Projects Page
        print("  [3/6] Projects...")
        await page.goto(f"{BASE_URL}/projects")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(1)
        await page.screenshot(
            path=SCREENSHOTS_DIR / "projects.png",
            full_page=False
        )

        # 4. Issues Page
        print("  [4/6] Issues...")
        await page.goto(f"{BASE_URL}/issues")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(1)
        await page.screenshot(
            path=SCREENSHOTS_DIR / "issues.png",
            full_page=False
        )

        # 5. New Session Modal (with auto-fill)
        print("  [5/6] New Session Modal...")
        await page.goto(f"{BASE_URL}/")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(0.5)
        # Click new session button
        new_session_btn = page.locator('button:has-text("New Session")')
        if await new_session_btn.count() > 0:
            await new_session_btn.click()
            await asyncio.sleep(1)  # Wait for auto-fill
        await page.screenshot(
            path=SCREENSHOTS_DIR / "new-session-modal.png",
            full_page=False
        )
        # Close modal
        await page.keyboard.press("Escape")

        # 6. Update Modal
        print("  [6/6] Update Modal...")
        # Click version indicator
        version_indicator = page.locator('.update-indicator, .version-badge, [id*="update"]').first
        if await version_indicator.count() > 0:
            await version_indicator.click()
            await asyncio.sleep(1)
            await page.screenshot(
                path=SCREENSHOTS_DIR / "update-modal.png",
                full_page=False
            )

        # 7. Hero screenshot (full width, cropped)
        print("  [BONUS] Hero image...")
        await page.goto(f"{BASE_URL}/")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(1)
        await page.screenshot(
            path=SCREENSHOTS_DIR / "hero.png",
            full_page=False
        )

        await browser.close()

    print(f"\nScreenshots saved to: {SCREENSHOTS_DIR}")
    print("Files created:")
    for f in sorted(SCREENSHOTS_DIR.glob("*.png")):
        print(f"  - {f.name}")


if __name__ == "__main__":
    asyncio.run(take_screenshots())
