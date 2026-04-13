"""
Action models — represent what the agent can do in the browser.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Action:
    """A single action the agent wants to perform."""
    action_type: str    # "click", "fill", "select", "navigate", "hover", "press_key",
                        # "submit_form", "go_back", "wait", "scroll_to", "scroll_down",
                        # "scroll_up", "switch_iframe", "switch_main", "upload_file", "drag_drop"
    selector: str = ""
    value: str = ""
    url: str = ""       # for navigate actions
    description: str = ""  # human-readable: "Click the Login button"
    target_selector: str = ""  # for drag_drop: drop target


@dataclass
class ActionResult:
    """Result of executing an action."""
    action: Action
    success: bool
    url_before: str = ""
    url_after: str = ""
    title_after: str = ""
    errors_after: str = ""  # observer error summary after action
    page_changed: bool = False  # did the URL or visible content change?
