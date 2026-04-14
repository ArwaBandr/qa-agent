"""
Planner — Uses the LLM to decide what actions to take next.
Analyzes the current page, understands its purpose, and plans test actions.
"""
from __future__ import annotations

from typing import Optional
from agent.brain import LLMClient
from agent.prompts import EXPLORER_SYSTEM, LOGIN_SYSTEM, FLOW_DISCOVERY_SYSTEM, FLOW_STEP_SYSTEM
from models.action import Action
from models.page_state import PageState


class Planner:
    """Asks the LLM what to do next based on the current page state."""

    def __init__(self, llm: LLMClient, focus_area: str = ""):
        self.llm = llm
        self.focus_area = focus_area
        self._visited_pages: list[str] = []
        self._tested_actions: list[str] = []
        self._form_memory: dict[str, dict[str, str]] = {}  # url -> {selector: value}

    def record_fill(self, url: str, selector: str, value: str):
        """Record that a field was filled, for form memory context."""
        if url not in self._form_memory:
            self._form_memory[url] = {}
        self._form_memory[url][selector] = value

    def get_form_memory_context(self, url: str) -> str:
        """Get form memory context string for the current page."""
        fills = self._form_memory.get(url, {})
        if not fills:
            return ""
        lines = ["Previously filled fields on this page:"]
        for sel, val in fills.items():
            lines.append(f"  - {sel} = {val!r}")
        return "\n".join(lines)

    def plan_next_actions(
        self,
        page_state: PageState,
        exploration_history: str = "",
    ) -> dict:
        """Given current page state, return LLM's plan: understanding + actions."""
        user_message = self._build_planner_message(page_state, exploration_history)

        # Build system prompt with focus and form memory
        focus_instruction = ""
        if self.focus_area:
            focus_instruction = (
                f"FOCUS AREA: The tester wants you to prioritize testing: {self.focus_area}\n"
                f"Concentrate your actions on this area. Still explore other things if relevant, "
                f"but give priority to the focus area."
            )

        form_memory = self.get_form_memory_context(page_state.url)
        form_memory_context = ""
        if form_memory:
            form_memory_context = (
                f"Form memory (fields you already filled on this page — avoid re-filling the same "
                f"fields with the same values, try different test data instead):\n{form_memory}"
            )

        system_prompt = EXPLORER_SYSTEM.format(
            focus_instruction=focus_instruction,
            form_memory_context=form_memory_context,
        )

        result = self.llm.chat_json(
            system_prompt=system_prompt,
            user_message=user_message,
        )

        if result.get("parse_error"):
            return {
                "page_understanding": "Could not parse LLM response",
                "observations": [],
                "next_actions": [],
                "testing_strategy": "",
                "raw_response": result.get("raw_response", ""),
            }

        # Convert raw action dicts to Action objects
        actions = []
        for raw_action in result.get("next_actions", []):
            actions.append(Action(
                action_type=raw_action.get("action_type", "click"),
                selector=raw_action.get("selector", ""),
                value=raw_action.get("value", ""),
                url=raw_action.get("url", ""),
                description=raw_action.get("description", ""),
                target_selector=raw_action.get("target_selector", ""),
            ))

        self._visited_pages.append(page_state.url)
        for action in actions:
            self._tested_actions.append(f"{action.action_type}: {action.description}")

        return {
            "page_understanding": result.get("page_understanding", ""),
            "observations": result.get("observations", []),
            "next_actions": actions,
            "testing_strategy": result.get("testing_strategy", ""),
        }

    def discover_flows(self, page_state: PageState, exploration_history: str = "") -> list[dict]:
        """Ask the LLM to identify end-to-end user flows worth testing."""
        focus_instruction = ""
        if self.focus_area:
            focus_instruction = (
                f"FOCUS AREA: Prioritize flows related to: {self.focus_area}"
            )

        system_prompt = FLOW_DISCOVERY_SYSTEM.format(focus_instruction=focus_instruction)

        parts = ["Identify testable end-to-end user flows on this application.\n"]
        parts.append(f"Current page state:\n{page_state.to_llm_context()}\n")
        if exploration_history:
            parts.append(f"Exploration history:\n{exploration_history}\n")
        parts.append("Respond with the flows JSON.")

        # Flow discovery is a simpler task — use the fast model
        result = self.llm.chat_json_fast(
            system_prompt=system_prompt,
            user_message="\n".join(parts),
        )

        if result.get("parse_error"):
            return []

        flows = result.get("flows", [])
        # Filter to high/medium priority
        return [f for f in flows if f.get("priority") in ("high", "medium")]

    def plan_flow_step(
        self,
        flow_goal: str,
        expected_outcome: str,
        steps_so_far: list[str],
        page_state: PageState,
    ) -> dict:
        """Given a flow goal and current state, return the next action for the flow."""
        steps_text = "\n".join(steps_so_far) if steps_so_far else "(None yet — this is the first step)"

        system_prompt = FLOW_STEP_SYSTEM.replace("{{flow_goal}}", flow_goal).replace(
            "{{expected_outcome}}", expected_outcome
        ).replace("{{steps_so_far}}", steps_text)

        user_message = (
            f"Continue executing the flow. Here is the current page state:\n\n"
            f"{page_state.to_compact_context()}\n\n"
            f"What is the next action to take?"
        )

        # Flow steps are simpler decisions — use the fast model
        result = self.llm.chat_json_fast(
            system_prompt=system_prompt,
            user_message=user_message,
        )

        if result.get("parse_error"):
            return {"flow_status": "blocked", "blocked_reason": "Could not parse LLM response"}

        # Extract action
        raw_action = result.get("action", {})
        action = Action(
            action_type=raw_action.get("action_type", "click"),
            selector=raw_action.get("selector", ""),
            value=raw_action.get("value", ""),
            url=raw_action.get("url", ""),
            description=raw_action.get("description", ""),
            target_selector=raw_action.get("target_selector", ""),
        )

        self._tested_actions.append(f"[flow] {action.action_type}: {action.description}")

        return {
            "action": action,
            "flow_status": result.get("flow_status", "in_progress"),
            "progress_note": result.get("progress_note", ""),
            "blocked_reason": result.get("blocked_reason", ""),
        }

    def identify_login(self, page_state: PageState) -> dict:
        """Ask the LLM to identify login form fields on the current page."""
        user_message = (
            "Analyze this page and identify the login form fields.\n\n"
            f"Page state:\n{page_state.to_llm_context()}"
        )

        # Login identification is straightforward — use the fast model
        return self.llm.chat_json_fast(
            system_prompt=LOGIN_SYSTEM,
            user_message=user_message,
        )

    def get_exploration_summary(self) -> str:
        """Summary of what's been explored so far, for context in future plans."""
        parts = []

        if self._visited_pages:
            parts.append(f"Pages visited ({len(self._visited_pages)}):")
            for url in self._visited_pages[-10:]:  # only last 10 to save tokens
                parts.append(f"  - {url}")

        if self._tested_actions:
            parts.append(f"\nRecent actions ({len(self._tested_actions)} total):")
            for action in self._tested_actions[-15:]:  # only last 15 to save tokens
                parts.append(f"  - {action}")

        return "\n".join(parts) if parts else "No exploration history yet."

    def _build_planner_message(self, page_state: PageState, exploration_history: str) -> str:
        parts = ["Analyze this page and decide what to test next.\n"]

        parts.append(f"Current page state:\n{page_state.to_llm_context()}\n")

        if exploration_history:
            parts.append(f"Exploration history so far:\n{exploration_history}\n")

        parts.append(
            "Based on the page state, respond with your analysis "
            "and planned actions in the required JSON format."
        )

        return "\n".join(parts)
