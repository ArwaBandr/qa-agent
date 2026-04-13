"""
Browser Engine — Playwright lifecycle, navigation, and interaction.
Manages a single browser session with stealth settings.
"""

import asyncio
import json
import os
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
)


STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class BrowserEngine:
    """Manages Playwright browser with stealth config and safe interaction methods."""

    def __init__(
        self,
        artifacts_dir: str = "artifacts",
        headless: bool = True,
        viewport_width: int = 1920,
        viewport_height: int = 1080,
        timeout: int = 30000,
        action_timeout: int = 10000,
    ):
        self.headless = headless
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.timeout = timeout
        self.action_timeout = action_timeout

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=STEALTH_USER_AGENT,
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            ignore_https_errors=True,
        )
        self._page = await self._context.new_page()

    async def stop(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def page(self) -> Page:
        return self._page

    # ── Navigation ─────────────────────────────────────────────

    async def goto(self, url: str) -> dict:
        """Navigate to a URL. Returns status dict."""
        try:
            response = await self._page.goto(
                url, wait_until="domcontentloaded", timeout=self.timeout
            )
            await self._page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            # networkidle can timeout on busy pages, that's ok
            pass

        try:
            status = response.status if response else None
        except Exception:
            status = None

        return {
            "success": status is None or (200 <= status < 400),
            "status": status,
            "url": self._page.url,
            "title": await self._page.title(),
        }

    async def go_back(self) -> bool:
        try:
            await self._page.go_back(wait_until="domcontentloaded", timeout=self.timeout)
            return True
        except Exception:
            return False

    async def current_url(self) -> str:
        return self._page.url

    async def current_title(self) -> str:
        return await self._page.title()

    # ── Interactions ───────────────────────────────────────────

    async def element_exists(self, selector: str) -> bool:
        """Check if an element matching the selector exists on the page."""
        try:
            el = await self._page.query_selector(selector)
            return el is not None
        except Exception:
            return False

    async def click(self, selector: str) -> dict:
        """Click an element exactly once. Detects navigation after the click."""
        # Check element exists first
        if not await self.element_exists(selector):
            return {"success": False, "action": "click", "selector": selector, "error": f"Element not found: {selector}"}

        url_before = self._page.url

        # Single click attempt with Playwright
        try:
            await self._page.click(selector, timeout=self.action_timeout)
        except Exception as e:
            # Fallback: JS click
            try:
                await self._page.evaluate(f"""
                    (() => {{
                        const el = document.querySelector({json.dumps(selector)});
                        if (el) el.click();
                    }})()
                """)
            except Exception:
                return {"success": False, "action": "click", "selector": selector, "error": str(e)[:200]}

        # Wait and detect if navigation happened
        await asyncio.sleep(0.5)
        navigated = self._page.url != url_before
        if navigated:
            await self._wait_after_navigation()

        return {"success": True, "action": "click", "selector": selector, "navigated": navigated}

    async def _wait_after_navigation(self):
        """Wait for the page to be ready after a navigation event."""
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        try:
            await self._page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            pass

    async def fill(self, selector: str, value: str) -> dict:
        """Fill an input field with click-clear-type fallback chain."""
        # Attempt 1: Playwright fill
        try:
            await self._page.fill(selector, value, timeout=self.action_timeout)
            return {"success": True, "action": "fill", "selector": selector, "value": value}
        except Exception:
            pass

        # Attempt 2: Click, select all, type
        try:
            await self._page.click(selector, timeout=self.action_timeout)
            await self._page.keyboard.press("Control+A")
            await self._page.keyboard.press("Backspace")
            await self._page.keyboard.type(value, delay=30)
            return {"success": True, "action": "fill", "selector": selector, "value": value, "fallback": "type"}
        except Exception:
            pass

        # Attempt 3: JS value set + input/change events
        try:
            await self._page.evaluate(f"""
                (() => {{
                    const el = document.querySelector({json.dumps(selector)});
                    if (el) {{
                        el.focus();
                        el.value = {json.dumps(value)};
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                }})()
            """)
            return {"success": True, "action": "fill", "selector": selector, "value": value, "fallback": "js"}
        except Exception as e:
            return {"success": False, "action": "fill", "selector": selector, "error": str(e)[:200]}

    async def select_option(self, selector: str, value: str) -> dict:
        """Select a dropdown option."""
        try:
            await self._page.select_option(selector, value, timeout=self.action_timeout)
            return {"success": True, "action": "select", "selector": selector, "value": value}
        except Exception as e:
            return {"success": False, "action": "select", "selector": selector, "error": str(e)[:200]}

    async def hover(self, selector: str) -> dict:
        """Hover over an element."""
        try:
            await self._page.hover(selector, timeout=self.action_timeout)
            return {"success": True, "action": "hover", "selector": selector}
        except Exception as e:
            return {"success": False, "action": "hover", "selector": selector, "error": str(e)[:200]}

    async def press_key(self, key: str) -> dict:
        """Press a keyboard key."""
        try:
            await self._page.keyboard.press(key)
            return {"success": True, "action": "press_key", "key": key}
        except Exception as e:
            return {"success": False, "action": "press_key", "key": key, "error": str(e)[:200]}

    # ── Scroll ─────────────────────────────────────────────────

    async def scroll_to_element(self, selector: str) -> dict:
        """Scroll an element into view."""
        try:
            await self._page.evaluate(f"""
                (() => {{
                    const el = document.querySelector({json.dumps(selector)});
                    if (el) el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                }})()
            """)
            await asyncio.sleep(0.5)
            return {"success": True, "action": "scroll_to", "selector": selector}
        except Exception as e:
            return {"success": False, "action": "scroll_to", "selector": selector, "error": str(e)[:200]}

    async def scroll_down(self, pixels: int = 500) -> dict:
        """Scroll the page down by a number of pixels."""
        try:
            await self._page.evaluate(f"window.scrollBy(0, {pixels})")
            await asyncio.sleep(0.3)
            return {"success": True, "action": "scroll_down", "pixels": pixels}
        except Exception as e:
            return {"success": False, "action": "scroll_down", "error": str(e)[:200]}

    async def scroll_up(self, pixels: int = 500) -> dict:
        """Scroll the page up by a number of pixels."""
        try:
            await self._page.evaluate(f"window.scrollBy(0, -{pixels})")
            await asyncio.sleep(0.3)
            return {"success": True, "action": "scroll_up", "pixels": pixels}
        except Exception as e:
            return {"success": False, "action": "scroll_up", "error": str(e)[:200]}

    # ── Iframe ─────────────────────────────────────────────────

    async def switch_to_iframe(self, selector: str) -> dict:
        """Switch context to an iframe element."""
        try:
            frame_element = await self._page.query_selector(selector)
            if not frame_element:
                return {"success": False, "action": "switch_iframe", "error": f"Iframe not found: {selector}"}
            frame = await frame_element.content_frame()
            if not frame:
                return {"success": False, "action": "switch_iframe", "error": "Could not access iframe content"}
            self._iframe_page = self._page
            self._page = frame
            return {"success": True, "action": "switch_iframe", "selector": selector}
        except Exception as e:
            return {"success": False, "action": "switch_iframe", "error": str(e)[:200]}

    async def switch_to_main(self) -> dict:
        """Switch back to the main page from an iframe."""
        if hasattr(self, '_iframe_page') and self._iframe_page:
            self._page = self._iframe_page
            self._iframe_page = None
            return {"success": True, "action": "switch_main"}
        return {"success": True, "action": "switch_main"}

    # ── File Upload ────────────────────────────────────────────

    async def upload_file(self, selector: str, file_path: str) -> dict:
        """Upload a file to a file input element."""
        try:
            await self._page.set_input_files(selector, file_path, timeout=self.action_timeout)
            return {"success": True, "action": "upload_file", "selector": selector, "file": file_path}
        except Exception as e:
            return {"success": False, "action": "upload_file", "selector": selector, "error": str(e)[:200]}

    # ── Drag and Drop ──────────────────────────────────────────

    async def drag_and_drop(self, source_selector: str, target_selector: str) -> dict:
        """Drag an element from source to target."""
        try:
            await self._page.drag_and_drop(
                source_selector, target_selector, timeout=self.action_timeout
            )
            return {"success": True, "action": "drag_drop", "source": source_selector, "target": target_selector}
        except Exception as e:
            # Fallback: manual mouse drag
            try:
                src = await self._page.query_selector(source_selector)
                tgt = await self._page.query_selector(target_selector)
                if not src or not tgt:
                    return {"success": False, "action": "drag_drop", "error": "Source or target not found"}
                src_box = await src.bounding_box()
                tgt_box = await tgt.bounding_box()
                if not src_box or not tgt_box:
                    return {"success": False, "action": "drag_drop", "error": "Cannot get element positions"}
                await self._page.mouse.move(
                    src_box["x"] + src_box["width"] / 2,
                    src_box["y"] + src_box["height"] / 2,
                )
                await self._page.mouse.down()
                await self._page.mouse.move(
                    tgt_box["x"] + tgt_box["width"] / 2,
                    tgt_box["y"] + tgt_box["height"] / 2,
                    steps=10,
                )
                await self._page.mouse.up()
                return {"success": True, "action": "drag_drop", "source": source_selector, "target": target_selector, "fallback": True}
            except Exception as e2:
                return {"success": False, "action": "drag_drop", "error": str(e2)[:200]}

    # ── Session ────────────────────────────────────────────────

    async def check_session_alive(self) -> bool:
        """Check if the browser session is still valid (page not crashed, not logged out)."""
        try:
            url = self._page.url
            title = await self._page.title()
            # Check for common session-expired indicators
            text = await self._page.evaluate("document.body?.innerText?.slice(0, 500) || ''")
            session_dead_signals = [
                "session expired", "session timeout", "logged out",
                "sign in", "log in", "login", "unauthorized", "403",
            ]
            text_lower = text.lower()
            # Only flag if the page looks like a login/error page (not just has "login" in nav)
            if any(s in text_lower for s in ["session expired", "session timeout", "logged out"]):
                return False
            return True
        except Exception:
            return False

    # ── Page Info ──────────────────────────────────────────────

    async def get_page_text(self) -> str:
        """Get visible text content of the page."""
        try:
            return await self._page.evaluate("document.body?.innerText || ''")
        except Exception:
            return ""

    async def get_page_html(self) -> str:
        """Get the outer HTML of the page."""
        try:
            return await self._page.evaluate("document.documentElement.outerHTML")
        except Exception:
            return ""

    async def wait_for_stable(self, timeout: int = 2000):
        """Wait for the page to become visually stable (no new network requests)."""
        try:
            await self._page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass
