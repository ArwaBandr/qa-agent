"""
Observer — Captures network traffic, console logs, JS errors, and dialogs
using Playwright's built-in event system.
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from playwright.async_api import Page, Response, ConsoleMessage, Dialog


@dataclass
class NetworkEntry:
    url: str
    method: str
    status: Optional[int] = None
    status_text: str = ""
    resource_type: str = ""
    error: Optional[str] = None
    timestamp: float = 0.0


@dataclass
class ConsoleEntry:
    level: str          # "log", "warning", "error", "info"
    text: str
    url: str = ""
    timestamp: float = 0.0


@dataclass
class PageError:
    message: str
    url: str = ""
    timestamp: float = 0.0


@dataclass
class DialogEvent:
    dialog_type: str    # "alert", "confirm", "prompt", "beforeunload"
    message: str
    timestamp: float = 0.0


class Observer:
    """Attaches to a Playwright Page and records everything that happens."""

    def __init__(self):
        self.network_log: list[NetworkEntry] = []
        self.console_log: list[ConsoleEntry] = []
        self.page_errors: list[PageError] = []
        self.dialogs: list[DialogEvent] = []
        self._page: Optional[Page] = None

    def attach(self, page: Page):
        """Start observing a page's events."""
        self._page = page
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("dialog", self._on_dialog)

    def detach(self):
        """Stop observing."""
        if self._page:
            self._page.remove_listener("response", self._on_response)
            self._page.remove_listener("requestfailed", self._on_request_failed)
            self._page.remove_listener("console", self._on_console)
            self._page.remove_listener("pageerror", self._on_page_error)
            self._page.remove_listener("dialog", self._on_dialog)
            self._page = None

    # ── Snapshots ──────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a summary of everything captured since last reset."""
        return {
            "network_errors": [
                {"url": e.url, "method": e.method, "status": e.status, "error": e.error}
                for e in self.network_log
                if (e.status and e.status >= 400) or e.error
            ],
            "console_errors": [
                {"level": e.level, "text": e.text[:300]}
                for e in self.console_log
                if e.level in ("error", "warning")
            ],
            "page_errors": [
                {"message": e.message[:300]}
                for e in self.page_errors
            ],
            "dialogs": [
                {"type": d.dialog_type, "message": d.message[:200]}
                for d in self.dialogs
            ],
        }

    def get_errors_summary(self) -> str:
        """Human-readable summary of errors for the LLM."""
        snap = self.snapshot()
        parts = []

        if snap["network_errors"]:
            parts.append("Network errors:")
            for e in snap["network_errors"][:10]:
                if e["error"]:
                    parts.append(f"  - {e['method']} {e['url'][:80]} failed: {e['error']}")
                else:
                    parts.append(f"  - {e['method']} {e['url'][:80]} -> HTTP {e['status']}")

        if snap["page_errors"]:
            parts.append("JavaScript errors:")
            for e in snap["page_errors"][:10]:
                parts.append(f"  - {e['message'][:150]}")

        if snap["console_errors"]:
            # Only show console errors not already covered by page_errors
            page_err_texts = {e["message"][:50] for e in snap["page_errors"]}
            console_only = [
                e for e in snap["console_errors"]
                if e["level"] == "error" and e["text"][:50] not in page_err_texts
            ]
            if console_only:
                parts.append("Console errors:")
                for e in console_only[:10]:
                    parts.append(f"  - {e['text'][:150]}")

        if snap["dialogs"]:
            parts.append("Unexpected dialogs:")
            for d in snap["dialogs"]:
                parts.append(f"  - {d['type']}: {d['message'][:100]}")

        return "\n".join(parts) if parts else "No errors detected."

    def reset(self):
        """Clear all captured data."""
        self.network_log.clear()
        self.console_log.clear()
        self.page_errors.clear()
        self.dialogs.clear()

    def has_errors(self) -> bool:
        return bool(self.page_errors) or any(
            (e.status and e.status >= 400) or e.error for e in self.network_log
        )

    # ── Event Handlers ─────────────────────────────────────────

    def _on_response(self, response: Response):
        entry = NetworkEntry(
            url=response.url,
            method=response.request.method,
            status=response.status,
            status_text=response.status_text,
            resource_type=response.request.resource_type,
            timestamp=time.time(),
        )
        self.network_log.append(entry)

    def _on_request_failed(self, request):
        entry = NetworkEntry(
            url=request.url,
            method=request.method,
            resource_type=request.resource_type,
            error=request.failure or "Unknown failure",
            timestamp=time.time(),
        )
        self.network_log.append(entry)

    def _on_console(self, message: ConsoleMessage):
        entry = ConsoleEntry(
            level=message.type,
            text=message.text,
            url=message.location.get("url", "") if hasattr(message, "location") else "",
            timestamp=time.time(),
        )
        self.console_log.append(entry)

    def _on_page_error(self, error):
        entry = PageError(
            message=str(error),
            url=self._page.url if self._page else "",
            timestamp=time.time(),
        )
        self.page_errors.append(entry)

    async def _on_dialog(self, dialog: Dialog):
        event = DialogEvent(
            dialog_type=dialog.type,
            message=dialog.message,
            timestamp=time.time(),
        )
        self.dialogs.append(event)
        await dialog.dismiss()
