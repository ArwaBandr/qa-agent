"""
Page State — a snapshot of everything the LLM needs to know about the current page.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PageState:
    """Complete snapshot of a page for the LLM to reason about."""
    url: str
    title: str
    visible_text_summary: str = ""
    headings: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    buttons: list[dict] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    selects: list[dict] = field(default_factory=list)
    forms: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    errors_summary: str = ""
    visual_issues: list[dict] = field(default_factory=list)

    def to_llm_context(self) -> str:
        """Format this page state as text context for the LLM."""
        parts = [
            f"URL: {self.url}",
            f"Title: {self.title}",
        ]

        if self.headings:
            parts.append("\nPage structure:")
            for h in self.headings[:15]:
                indent = "  " * (h.get("level", 1) - 1)
                parts.append(f"  {indent}H{h['level']}: {h['text']}")

        if self.visible_text_summary:
            parts.append(f"\nVisible text (first 500 chars):\n  {self.visible_text_summary[:500]}")

        if self.links:
            parts.append(f"\nLinks ({len(self.links)} total):")
            for link in self.links[:30]:
                parts.append(f"  - [{link.get('text', '?')[:50]}] -> {link.get('href', '')[:80]}  (selector: {link.get('selector', '')})")

        if self.buttons:
            parts.append(f"\nButtons ({len(self.buttons)} total):")
            for btn in self.buttons[:20]:
                disabled = " [DISABLED]" if btn.get("disabled") else ""
                parts.append(f"  - \"{btn.get('text', '?')[:50]}\"{disabled}  (selector: {btn.get('selector', '')})")

        if self.inputs:
            parts.append(f"\nInput fields ({len(self.inputs)} total):")
            for inp in self.inputs[:20]:
                label = inp.get("label") or inp.get("placeholder") or inp.get("name") or inp.get("type", "text")
                required = " [REQUIRED]" if inp.get("required") else ""
                parts.append(f"  - {label} (type={inp.get('type', 'text')}){required}  (selector: {inp.get('selector', '')})")

        if self.selects:
            parts.append(f"\nDropdowns ({len(self.selects)} total):")
            for sel in self.selects[:10]:
                opts = [o.get("text", "") for o in sel.get("options", [])[:5]]
                parts.append(f"  - {sel.get('name', '?')}: [{', '.join(opts)}...]  (selector: {sel.get('selector', '')})")

        if self.forms:
            parts.append(f"\nForms ({len(self.forms)} total):")
            for form in self.forms[:10]:
                parts.append(f"  - {form.get('method', 'GET')} {form.get('action', '?')} ({form.get('field_count', 0)} fields)  (selector: {form.get('selector', '')})")

        if self.images:
            broken = [img for img in self.images if img.get("broken")]
            if broken:
                parts.append(f"\nBroken images ({len(broken)}):")
                for img in broken[:10]:
                    parts.append(f"  - {img.get('src', '?')[:80]} (alt: {img.get('alt', 'none')})")

        if self.visual_issues:
            parts.append(f"\nVisual issues detected:")
            for issue in self.visual_issues[:10]:
                parts.append(f"  - [{issue.get('type', '?')}] {issue.get('description', '')}")

        if self.errors_summary and self.errors_summary != "No errors detected.":
            parts.append(f"\nErrors:\n{self.errors_summary}")

        return "\n".join(parts)

    def to_compact_context(self) -> str:
        """Compact page context for flow steps — fewer tokens, just interactive elements."""
        parts = [
            f"URL: {self.url}",
            f"Title: {self.title}",
        ]

        if self.headings:
            parts.append("Headings: " + " | ".join(
                f"H{h['level']}: {h['text']}" for h in self.headings[:5]
            ))

        if self.visible_text_summary:
            parts.append(f"Text: {self.visible_text_summary[:200]}")

        if self.buttons:
            parts.append(f"Buttons ({len(self.buttons)}):")
            for btn in self.buttons[:10]:
                disabled = " [DISABLED]" if btn.get("disabled") else ""
                parts.append(f"  - \"{btn.get('text', '?')[:30]}\"{disabled}  ({btn.get('selector', '')})")

        if self.inputs:
            parts.append(f"Inputs ({len(self.inputs)}):")
            for inp in self.inputs[:10]:
                label = inp.get("label") or inp.get("placeholder") or inp.get("name") or inp.get("type", "text")
                val = f" val={inp['value']!r}" if inp.get("value") else ""
                parts.append(f"  - {label} (type={inp.get('type', 'text')}){val}  ({inp.get('selector', '')})")

        if self.selects:
            parts.append(f"Dropdowns ({len(self.selects)}):")
            for sel in self.selects[:5]:
                parts.append(f"  - {sel.get('name', '?')}  ({sel.get('selector', '')})")

        if self.links:
            # Only show key navigation links, not all 30
            parts.append(f"Links ({len(self.links)} total, showing key ones):")
            for link in self.links[:8]:
                parts.append(f"  - [{link.get('text', '?')[:30]}] ({link.get('selector', '')})")

        if self.errors_summary and self.errors_summary != "No errors detected.":
            parts.append(f"Errors: {self.errors_summary[:200]}")

        return "\n".join(parts)
