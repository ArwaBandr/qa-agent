"""
Judge — Uses the LLM to evaluate action results and detect bugs.
Compares before/after state and decides if something went wrong.
"""
from __future__ import annotations

from typing import Optional
from agent.brain import LLMClient
from agent.prompts import JUDGE_SYSTEM
from models.action import Action, ActionResult
from models.page_state import PageState
from models.bug import BugReport


class Judge:
    """Evaluates action results and flags bugs."""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.bugs_found: list[BugReport] = []
        self._seen_bug_titles: set[str] = set()

    def _is_agent_failure(self, action_result: ActionResult) -> bool:
        """Check if a failure is caused by the agent/automation, not the app."""
        if action_result.success:
            return False
        error = action_result.errors_after.lower() if action_result.errors_after else ""
        action_type = action_result.action.action_type
        # Selector/timeout/stale issues are agent problems
        agent_signals = ["not found", "selector", "timeout", "stale", "detached", "no element", "cannot find"]
        if any(s in error for s in agent_signals):
            return True
        # Fill/scroll/iframe/upload failures are usually automation issues
        if action_type in ("fill", "scroll_to", "scroll_down", "scroll_up", "switch_iframe", "upload_file", "drag_drop"):
            if not action_result.success:
                return True
        return False

    def _has_signals(self, action_result: ActionResult, state_before: PageState, state_after: PageState) -> bool:
        """Check if there are any signals worth judging (errors, failures, visual issues)."""
        # Agent failures are not real bugs — skip them
        if self._is_agent_failure(action_result):
            return False
        if not action_result.success:
            return True
        if action_result.errors_after and action_result.errors_after != "No errors detected.":
            return True
        if state_after.visual_issues:
            return True
        # Action did nothing — button/form that silently fails
        if not action_result.page_changed and action_result.action.action_type in ("click", "submit_form"):
            return True
        return False

    def evaluate(
        self,
        action: Action,
        action_result: ActionResult,
        state_before: PageState,
        state_after: PageState,
        step_descriptions: list[str],
    ) -> Optional[BugReport]:
        """Evaluate an action result. Returns a BugReport if a bug is found, else None."""
        # Skip LLM call if action succeeded cleanly with no error signals
        if not self._has_signals(action_result, state_before, state_after):
            return None

        user_message = self._build_judge_message(
            action, action_result, state_before, state_after
        )

        # Use fast model for judging — it's a simpler yes/no task
        result = self.llm.chat_json_fast(
            system_prompt=JUDGE_SYSTEM,
            user_message=user_message,
        )

        if result.get("parse_error"):
            return None

        if not result.get("is_bug", False):
            return None

        confidence = result.get("confidence", 0.0)
        if confidence < 0.5:
            return None

        bug_data = result.get("bug_report", {})
        title = bug_data.get("title", "Unknown issue")

        # Deduplicate: exact title match OR same page + same bug type + same element
        if title in self._seen_bug_titles:
            return None
        # Fuzzy dedup: same page URL + bug type + similar action = same bug
        bug_type = bug_data.get("bug_type", "functional")
        dedup_key = f"{state_after.url}|{bug_type}|{action.selector}"
        if dedup_key in self._seen_bug_titles:
            return None
        self._seen_bug_titles.add(title)
        self._seen_bug_titles.add(dedup_key)

        bug = BugReport(
            title=title,
            bug_type=bug_data.get("bug_type", "functional"),
            severity=bug_data.get("severity", "medium"),
            page_url=state_after.url,
            steps=step_descriptions,
            expected=bug_data.get("expected", ""),
            actual=bug_data.get("actual", ""),
            evidence=bug_data.get("evidence", []),
            notes=bug_data.get("notes", ""),
        )

        self.bugs_found.append(bug)
        return bug

    def evaluate_page_load(
        self,
        page_state: PageState,
        step_descriptions: list[str],
    ) -> Optional[BugReport]:
        """Evaluate a page load without a prior action (e.g., initial navigation)."""
        if not page_state.errors_summary or page_state.errors_summary == "No errors detected.":
            if not page_state.visual_issues:
                return None

        user_message = (
            "A page was loaded. Check if there are any issues.\n\n"
            f"Page state:\n{page_state.to_llm_context()}\n\n"
            "There was no specific user action — this is the result of navigating to the page.\n"
            "Look for: JS errors, failed network requests, broken images, visual issues, "
            "missing content, or anything that looks wrong.\n\n"
            "Respond in the required JSON format."
        )

        result = self.llm.chat_json_fast(
            system_prompt=JUDGE_SYSTEM,
            user_message=user_message,
        )

        if result.get("parse_error") or not result.get("is_bug", False):
            return None

        if result.get("confidence", 0.0) < 0.5:
            return None

        bug_data = result.get("bug_report", {})
        title = bug_data.get("title", "Unknown issue")

        if title in self._seen_bug_titles:
            return None
        self._seen_bug_titles.add(title)

        bug = BugReport(
            title=title,
            bug_type=bug_data.get("bug_type", "error"),
            severity=bug_data.get("severity", "medium"),
            page_url=page_state.url,
            steps=step_descriptions,
            expected=bug_data.get("expected", ""),
            actual=bug_data.get("actual", ""),
            evidence=bug_data.get("evidence", []),
            notes=bug_data.get("notes", ""),
        )

        self.bugs_found.append(bug)
        return bug

    def _build_judge_message(
        self,
        action: Action,
        action_result: ActionResult,
        state_before: PageState,
        state_after: PageState,
    ) -> str:
        parts = []

        parts.append(f"Action performed: {action.description}")
        parts.append(f"Action type: {action.action_type}")
        if action.selector:
            parts.append(f"Selector: {action.selector}")
        if action.value:
            parts.append(f"Value: {action.value}")
        parts.append(f"Action success: {action_result.success}")
        parts.append(f"Page changed: {action_result.page_changed}")
        parts.append(f"URL before: {action_result.url_before}")
        parts.append(f"URL after: {action_result.url_after}")

        parts.append(f"\n--- PAGE STATE BEFORE ---\n{state_before.to_llm_context()}")
        parts.append(f"\n--- PAGE STATE AFTER ---\n{state_after.to_llm_context()}")

        if action_result.errors_after and action_result.errors_after != "No errors detected.":
            parts.append(f"\n--- ERRORS AFTER ACTION ---\n{action_result.errors_after}")

        parts.append(
            "\nCompare the before and after page states and determine if anything went wrong.\n"
            "Respond in the required JSON format."
        )

        return "\n".join(parts)
