"""
Bug Report model — what the agent outputs when it finds an issue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BugReport:
    """A bug found during exploratory testing."""
    title: str
    bug_type: str           # "functional", "visual", "error", "ux"
    severity: str           # "critical", "high", "medium", "low"
    page_url: str
    steps: list[str]        # numbered steps to reproduce
    expected: str
    actual: str
    evidence: list[str] = field(default_factory=list)  # network errors, console errors, etc.
    notes: str = ""         # LLM's analysis/context

    def to_cli_output(self) -> str:
        """Format for CLI display."""
        lines = []
        lines.append("=" * 70)
        lines.append(f"  BUG: {self.title}")
        lines.append(f"  Type: {self.bug_type.upper()} | Severity: {self.severity.upper()}")
        lines.append(f"  Page: {self.page_url}")
        lines.append("-" * 70)

        lines.append("")
        lines.append("  Steps to Reproduce:")
        for step in self.steps:
            lines.append(f"    {step}")

        lines.append("")
        lines.append("  Expected:")
        lines.append(f"    {self.expected}")

        lines.append("")
        lines.append("  Actual:")
        for line in self.actual.split("\n"):
            lines.append(f"    {line}")

        if self.evidence:
            lines.append("")
            lines.append("  Evidence:")
            for e in self.evidence:
                lines.append(f"    - {e}")

        if self.notes:
            lines.append("")
            lines.append("  Notes:")
            lines.append(f"    {self.notes}")

        lines.append("=" * 70)
        return "\n".join(lines)

