"""
Explorer — Main exploration loop.
Ties together Browser, Planner, Judge, and Observer into an autonomous
exploratory testing agent.
"""

import asyncio
import json as _json
import os
import re
import time
from typing import Optional, Callable
from urllib.parse import urlparse, urljoin, urldefrag

from agent.brain import LLMClient
from agent.planner import Planner
from agent.judge import Judge
from browser.engine import BrowserEngine
from browser.observer import Observer
from browser.extractor import Extractor
from models.action import Action, ActionResult
from models.page_state import PageState
from models.bug import BugReport


# ── URL patterns ranked by testing value ──────────────────────

# High-value: pages with forms, user flows, data mutation
_HIGH_VALUE_PATTERNS = [
    r"/checkout", r"/cart", r"/payment", r"/order",
    r"/register", r"/signup", r"/sign-up", r"/login", r"/signin",
    r"/profile", r"/account", r"/settings", r"/preferences",
    r"/edit", r"/create", r"/new", r"/add", r"/update", r"/delete",
    r"/upload", r"/import", r"/export", r"/download",
    r"/search", r"/filter", r"/form", r"/submit",
    r"/dashboard", r"/admin", r"/manage",
]

# Medium-value: functional content pages
_MEDIUM_VALUE_PATTERNS = [
    r"/product", r"/item", r"/detail", r"/view",
    r"/list", r"/catalog", r"/category",
    r"/inbox", r"/message", r"/notification",
    r"/report", r"/analytics", r"/status",
]

# Low-value: static/info pages
_LOW_VALUE_PATTERNS = [
    r"/about", r"/contact", r"/help", r"/faq",
    r"/terms", r"/privacy", r"/policy", r"/legal",
    r"/blog", r"/news", r"/article", r"/press",
    r"/docs", r"/documentation", r"/guide",
]

# Link text keywords that boost priority
_HIGH_VALUE_LINK_TEXT = {
    "checkout", "cart", "buy", "purchase", "order", "pay",
    "sign up", "register", "create account",
    "submit", "save", "add", "upload", "delete", "edit",
    "search", "filter", "settings", "profile", "dashboard",
}

_LOW_VALUE_LINK_TEXT = {
    "about", "contact", "help", "faq", "terms", "privacy",
    "blog", "news", "press", "docs", "cookie", "legal",
}


def _score_url(href: str, link_text: str, focus_area: str) -> int:
    """Score a URL candidate. Higher = explore first."""
    score = 50  # baseline
    path = urlparse(href).path.lower()
    text = link_text.lower().strip()

    # URL pattern scoring
    for pattern in _HIGH_VALUE_PATTERNS:
        if re.search(pattern, path):
            score += 30
            break

    for pattern in _MEDIUM_VALUE_PATTERNS:
        if re.search(pattern, path):
            score += 15
            break

    for pattern in _LOW_VALUE_PATTERNS:
        if re.search(pattern, path):
            score -= 20
            break

    # Link text scoring
    for keyword in _HIGH_VALUE_LINK_TEXT:
        if keyword in text:
            score += 20
            break

    for keyword in _LOW_VALUE_LINK_TEXT:
        if keyword in text:
            score -= 15
            break

    # Penalize deep paths (too many segments = less important)
    depth = path.strip("/").count("/")
    if depth > 3:
        score -= 10

    # Penalize URLs with many query parameters (often pagination/sort variants)
    query = urlparse(href).query
    if query:
        param_count = query.count("&") + 1
        score -= param_count * 5

    # Boost if URL or link text matches focus area
    if focus_area:
        focus_lower = focus_area.lower()
        focus_words = focus_lower.split()
        for word in focus_words:
            if len(word) > 2:
                if word in path:
                    score += 40
                if word in text:
                    score += 30

    return score


class Explorer:
    """Autonomous exploratory testing agent."""

    def __init__(
        self,
        engine: BrowserEngine,
        llm: LLMClient,
        max_pages: int = 200,
        same_origin_only: bool = True,
        focus_area: str = "",
        on_status: Optional[Callable] = None,
        on_bug: Optional[Callable] = None,
    ):
        self.engine = engine
        self.planner = Planner(llm, focus_area=focus_area)
        self.judge = Judge(llm)
        self.observer = Observer()
        self.max_pages = max_pages
        self.same_origin_only = same_origin_only
        # Callbacks
        self._on_status = on_status or (lambda *a: None)
        self._on_bug = on_bug or (lambda *a: None)

        self._focus_area = focus_area

        # State tracking
        self._origin = ""
        self._visited_urls: set[str] = set()
        self._url_queue: list[tuple[int, str]] = []  # (priority_score, url) — highest first
        self._queued_norms: set[str] = set()  # fast dedup lookup
        self._pages_explored = 0
        self._step_log: list[str] = []  # human-readable steps for current flow
        self._global_step_num = 0
        self._consecutive_failures = 0  # track failures for graceful degradation

    async def run(self, start_url: str, auth: dict = None) -> list[BugReport]:
        """Run the full exploration. Returns all bugs found."""
        self._origin = urlparse(start_url).netloc

        self.observer.attach(self.engine.page)

        try:
            # Handle login if credentials provided
            if auth and auth.get("username") and auth.get("password"):
                await self._handle_login(start_url, auth)
            else:
                self._status("navigate", f"Opening {start_url}")
                result = await self.engine.goto(start_url)
                if not result["success"]:
                    self._status("error", f"Failed to load {start_url} (HTTP {result['status']})")
                    return self.judge.bugs_found

            # Phase 1: Explore the starting page (captures state, enqueues links)
            await self._explore_current_page()

            # Phase 2: Discover and execute end-to-end flows from the starting page
            await self._run_flow_phase()

            # Phase 3: Priority-based page exploration
            while self._url_queue and self._pages_explored < self.max_pages:
                # Graceful degradation: too many consecutive failures, skip ahead
                if self._consecutive_failures >= 5:
                    self._status("warning", "Too many consecutive failures, skipping to next page")
                    self._consecutive_failures = 0

                # Pop highest-priority URL
                _score, next_url = self._url_queue.pop(0)
                if self._normalize(next_url) in self._visited_urls:
                    continue

                # Session health check
                if not await self.engine.check_session_alive():
                    self._status("warning", "Session appears expired — stopping exploration")
                    break

                self._status("navigate", f"[{self._pages_explored}/{self.max_pages}] (score:{_score}) {next_url}")
                self._step_log = [f"1. Navigate to {next_url}"]
                self._global_step_num = 1

                try:
                    result = await self.engine.goto(next_url)
                    if not result["success"]:
                        self._status("warning", f"Failed to load: {next_url}")
                        continue
                    await self._explore_current_page()
                except Exception as e:
                    self._status("error", f"Error exploring {next_url}: {str(e)[:100]}")
                    self._consecutive_failures += 1
                    continue

        finally:
            self.observer.detach()

        return self.judge.bugs_found

    async def _explore_current_page(self):
        """Explore the current page: extract state, plan actions, execute, judge."""
        current_url = await self.engine.current_url()
        norm_url = self._normalize(current_url)

        if norm_url in self._visited_urls:
            return
        self._visited_urls.add(norm_url)
        self._pages_explored += 1

        # Extract page state
        state = await self._capture_page_state()
        self._status("analyze", f"Page: {state.title or current_url}")

        # Judge the page load itself
        load_bug = self.judge.evaluate_page_load(
            state, step_descriptions=list(self._step_log)
        )
        if load_bug:
            self._status("bug", f"Found: {load_bug.title}")
            self._on_bug(load_bug)

        # Collect links for later exploration
        self._enqueue_links(state)

        # Ask the LLM what to test
        history = self.planner.get_exploration_summary()
        plan = self.planner.plan_next_actions(state, exploration_history=history)

        if plan.get("page_understanding"):
            self._status("understand", plan["page_understanding"])

        if plan.get("observations"):
            for obs in plan["observations"][:5]:
                self._status("observe", obs)

        if plan.get("testing_strategy"):
            self._status("strategy", plan["testing_strategy"])

        # Execute planned actions — re-capture state between each action
        actions = plan.get("next_actions", [])
        no_selector_actions = ("navigate", "go_back", "wait", "press_key", "scroll_down", "scroll_up", "switch_main")
        current_state = state
        for action in actions:
            if self._pages_explored >= self.max_pages:
                break

            try:
                # Validate selector exists before executing (skip if no selector needed)
                if action.selector and action.action_type not in no_selector_actions:
                    exists = await self.engine.element_exists(action.selector)
                    if not exists:
                        # Retry: scroll down and check again (element might be below fold)
                        await self.engine.scroll_down(500)
                        await asyncio.sleep(0.3)
                        exists = await self.engine.element_exists(action.selector)
                        if not exists:
                            self._status("warning", f"Selector not found, skipping: {action.selector}")
                            self._consecutive_failures += 1
                            continue

                new_state = await self._execute_and_judge(action, current_state)
                self._consecutive_failures = 0  # Reset on success

                # Record fill actions in form memory
                if action.action_type == "fill" and action.selector and action.value:
                    self.planner.record_fill(
                        await self.engine.current_url(),
                        action.selector,
                        action.value,
                    )

                # After action, if we're on a new page, explore it recursively
                new_url = await self.engine.current_url()
                new_norm = self._normalize(new_url)
                if new_norm != norm_url and new_norm not in self._visited_urls:
                    await self._explore_current_page()
                    # Navigate back to continue testing the original page
                    await self.engine.go_back()
                    await self.engine.wait_for_stable()
                    # Re-capture state after going back
                    new_state = await self._capture_page_state()

                # Update state for the next action so selectors are fresh
                current_state = new_state

            except Exception as e:
                self._status("warning", f"Action error: {str(e)[:100]}")
                self._consecutive_failures += 1
                # Re-capture state and continue with next action
                try:
                    current_state = await self._capture_page_state()
                except Exception:
                    pass
                continue

    # ── Flow Testing ─────────────────────────────────────────────

    async def _run_flow_phase(self):
        """Discover and execute multi-step user flows."""
        state = await self._capture_page_state()
        history = self.planner.get_exploration_summary()

        self._status("strategy", "Discovering end-to-end user flows to test...")
        flows = self.planner.discover_flows(state, exploration_history=history)

        if not flows:
            self._status("strategy", "No multi-step flows identified — continuing with page exploration")
            return

        self._status("strategy", f"Found {len(flows)} flow(s) to test")

        for flow in flows:
            if self._pages_explored >= self.max_pages:
                break

            name = flow.get("name", "Unnamed flow")
            goal = flow.get("goal", "")
            expected = flow.get("expected_outcome", "")

            self._status("strategy", f"Starting flow: {name}")
            await self._execute_flow(name, goal, expected)

            # Navigate back to start page for next flow
            if flows.index(flow) < len(flows) - 1:
                start_url = state.url
                self._status("navigate", f"Returning to {start_url} for next flow")
                await self.engine.goto(start_url)
                await self.engine.wait_for_stable()

    async def _execute_flow(self, name: str, goal: str, expected_outcome: str):
        """Execute a single end-to-end flow, stepping through it action by action."""
        max_flow_steps = 15  # safety limit
        flow_steps: list[str] = []  # includes outcome annotations
        flow_failures = 0  # flow-local failure counter
        failed_actions: set[str] = set()  # track action+selector combos that failed/had no effect
        action_counts: dict[str, int] = {}  # track how many times each action+selector is attempted

        self._step_log = [f"1. Begin flow: {name}"]
        self._global_step_num = 1

        for step_num in range(max_flow_steps):
            if self._pages_explored >= self.max_pages:
                break

            # Capture current state
            current_state = await self._capture_page_state()

            # Mark new pages as visited
            norm = self._normalize(current_state.url)
            if norm not in self._visited_urls:
                self._visited_urls.add(norm)
                self._pages_explored += 1
                self._enqueue_links(current_state)

            # Ask LLM for next flow step
            step_result = self.planner.plan_flow_step(
                flow_goal=goal,
                expected_outcome=expected_outcome,
                steps_so_far=flow_steps,
                page_state=current_state,
            )

            status = step_result.get("flow_status", "in_progress")
            progress = step_result.get("progress_note", "")

            if status == "completed":
                self._status("strategy", f"Flow completed: {name} — {progress}")
                break

            if status == "blocked":
                reason = step_result.get("blocked_reason", "unknown reason")
                self._status("warning", f"Flow blocked: {name} — {reason}")
                break

            action = step_result.get("action")
            if not action:
                self._status("warning", f"Flow step returned no action — stopping flow")
                break

            if progress:
                self._status("observe", f"[FLOW] {progress}")

            # Detect repeated action — same action_type + selector already attempted AND failed
            action_key = f"{action.action_type}|{action.selector}"
            if action_key in failed_actions:
                self._status("warning", f"Flow: repeating a previously failed action, skipping: {action.description}")
                flow_steps.append(
                    f"{step_num + 1}. [SKIPPED-REPEAT] {action.description} — "
                    f"same action already failed/had no effect, likely a bug in the application"
                )
                flow_failures += 1
                if flow_failures >= 3:
                    self._status("warning", f"Flow abandoned: too many repeated/failed actions")
                    break
                continue

            # Detect LLM stuck in a loop — same action repeated 3+ times even if "succeeding"
            action_counts[action_key] = action_counts.get(action_key, 0) + 1
            if action_counts[action_key] >= 3:
                self._status("warning", f"Flow: action repeated 3+ times, LLM stuck — abandoning flow")
                flow_steps.append(
                    f"{step_num + 1}. [STUCK] {action.description} — "
                    f"LLM keeps repeating this action without advancing the flow"
                )
                break

            # Validate selector
            no_selector_actions = ("navigate", "go_back", "wait", "press_key", "scroll_down", "scroll_up", "switch_main")
            if action.selector and action.action_type not in no_selector_actions:
                exists = await self.engine.element_exists(action.selector)
                if not exists:
                    await self.engine.scroll_down(500)
                    await asyncio.sleep(0.3)
                    exists = await self.engine.element_exists(action.selector)
                    if not exists:
                        self._status("warning", f"Flow: selector not found: {action.selector}")
                        flow_steps.append(f"{step_num + 1}. [FAILED] {action.description} — selector not found")
                        failed_actions.add(action_key)
                        flow_failures += 1
                        if flow_failures >= 3:
                            self._status("warning", f"Flow abandoned: too many failures")
                            break
                        continue

            try:
                # Snapshot state before for effect detection
                text_before = current_state.visible_text_summary[:300]
                url_before = current_state.url

                # Execute and judge in flow context
                new_state = await self._execute_and_judge(action, current_state)

                # Detect if action had any effect
                text_after = new_state.visible_text_summary[:300]
                url_after = new_state.url
                had_effect = (url_before != url_after) or (text_before != text_after)

                # For fill actions: check if the input value actually changed
                if action.action_type == "fill" and action.selector and not had_effect:
                    try:
                        actual_val = await self.engine.page.evaluate(
                            f"document.querySelector({_json.dumps(action.selector)})?.value || ''"
                        )
                        if actual_val == action.value:
                            had_effect = True  # fill worked, just no visible text change
                    except Exception:
                        pass

                if had_effect:
                    flow_steps.append(f"{step_num + 1}. {action.description} — [OK]")
                    flow_failures = 0
                else:
                    # Action executed but nothing changed — likely a bug
                    flow_steps.append(
                        f"{step_num + 1}. {action.description} — [NO EFFECT] "
                        f"action executed but page did not change"
                    )
                    flow_failures += 1
                    failed_actions.add(action_key)
                    self._status("warning", f"Flow: action had no effect: {action.description}")

                    # Auto-report as bug if it's a meaningful action that should have done something
                    if action.action_type in ("click", "submit_form", "fill"):
                        self._report_flow_stuck_bug(
                            name=name,
                            action=action,
                            state=new_state,
                            flow_steps=flow_steps,
                        )

                    if flow_failures >= 3:
                        self._status("warning", f"Flow abandoned: actions keep having no effect")
                        break

                # Record fills
                if action.action_type == "fill" and action.selector and action.value:
                    self.planner.record_fill(
                        await self.engine.current_url(),
                        action.selector,
                        action.value,
                    )

            except Exception as e:
                self._status("warning", f"Flow action error: {str(e)[:100]}")
                flow_steps.append(f"{step_num + 1}. [ERROR] {action.description} — {str(e)[:80]}")
                failed_actions.add(action_key)
                flow_failures += 1
                if flow_failures >= 3:
                    self._status("warning", f"Flow abandoned: too many errors")
                    break

        else:
            self._status("warning", f"Flow hit step limit ({max_flow_steps}): {name}")

    def _report_flow_stuck_bug(
        self,
        name: str,
        action: Action,
        state: PageState,
        flow_steps: list[str],
    ):
        """Auto-report a bug when a flow action repeatedly has no effect."""
        title = f"Flow '{name}': {action.action_type} on {action.selector} has no effect"

        # Check dedup
        if title in self.judge._seen_bug_titles:
            return
        dedup_key = f"{state.url}|functional|{action.selector}"
        if dedup_key in self.judge._seen_bug_titles:
            return

        bug = BugReport(
            title=title,
            bug_type="functional",
            severity="high",
            page_url=state.url,
            steps=list(self._step_log) + flow_steps,
            expected=f"'{action.description}' should change the page state",
            actual=f"Action executed successfully but had no visible effect on the page",
            evidence=[
                f"Action type: {action.action_type}",
                f"Selector: {action.selector}",
                f"Value: {action.value}" if action.value else "",
                "Page state identical before and after action",
            ],
            notes=f"Detected during flow '{name}'. The action was executed without errors "
                  f"but the page did not respond. This indicates a functional bug where "
                  f"the UI element exists but its handler is broken or the state update fails.",
        )

        self.judge.bugs_found.append(bug)
        self.judge._seen_bug_titles.add(title)
        self.judge._seen_bug_titles.add(dedup_key)
        self._status("bug", f"Found: {title}")
        self._on_bug(bug)

    # ── Action Execution & Judging ────────────────────────────────

    async def _execute_and_judge(self, action: Action, state_before: PageState) -> PageState:
        """Execute a single action and have the judge evaluate the result.
        Returns the new page state after the action."""
        self._global_step_num += 1
        step_desc = f"{self._global_step_num}. {action.description}"
        self._step_log.append(step_desc)

        self._status("action", action.description)

        url_before = await self.engine.current_url()
        title_before = await self.engine.current_title()

        # Reset observer to capture only this action's effects
        self.observer.reset()

        # Execute the action
        exec_result = await self._execute_action(action)

        # Wait for page to settle (click already waits on navigation, but
        # other actions or DOM-only changes still need this)
        if not exec_result.get("navigated", False):
            await self.engine.wait_for_stable()

        # Capture after state
        url_after = await self.engine.current_url()
        title_after = await self.engine.current_title()
        errors_after = self.observer.get_errors_summary()

        state_after = await self._capture_page_state()

        # Detect page change: URL, title, or visible content changed
        url_changed = url_before != url_after
        title_changed = title_before != title_after
        content_changed = (
            state_before.visible_text_summary[:200] != state_after.visible_text_summary[:200]
        )
        page_changed = url_changed or title_changed or content_changed

        # Build action result
        action_result = ActionResult(
            action=action,
            success=exec_result.get("success", False),
            url_before=url_before,
            url_after=url_after,
            title_after=title_after,
            errors_after=errors_after,
            page_changed=page_changed,
        )

        if not exec_result.get("success", False):
            self._status("warning", f"Action failed: {exec_result.get('error', 'unknown')}")

        # Judge the result
        bug = self.judge.evaluate(
            action=action,
            action_result=action_result,
            state_before=state_before,
            state_after=state_after,
            step_descriptions=list(self._step_log),
        )

        if bug:
            self._status("bug", f"Found: {bug.title}")
            self._on_bug(bug)

        return state_after

    async def _execute_action(self, action: Action) -> dict:
        """Execute a single Action on the browser."""
        t = action.action_type

        if t == "click" or t == "submit_form":
            return await self.engine.click(action.selector)

        elif t == "fill":
            return await self.engine.fill(action.selector, action.value)

        elif t == "select":
            return await self.engine.select_option(action.selector, action.value)

        elif t == "hover":
            return await self.engine.hover(action.selector)

        elif t == "press_key":
            return await self.engine.press_key(action.value)

        elif t == "navigate":
            result = await self.engine.goto(action.url)
            return {"success": result["success"]}

        elif t == "go_back":
            ok = await self.engine.go_back()
            return {"success": ok}

        elif t == "wait":
            try:
                secs = float(action.value) if action.value else 2
            except ValueError:
                secs = 2
            await asyncio.sleep(min(secs, 10))
            return {"success": True}

        elif t == "scroll_to":
            return await self.engine.scroll_to_element(action.selector)

        elif t == "scroll_down":
            pixels = int(action.value) if action.value and action.value.isdigit() else 500
            return await self.engine.scroll_down(pixels)

        elif t == "scroll_up":
            pixels = int(action.value) if action.value and action.value.isdigit() else 500
            return await self.engine.scroll_up(pixels)

        elif t == "switch_iframe":
            return await self.engine.switch_to_iframe(action.selector)

        elif t == "switch_main":
            return await self.engine.switch_to_main()

        elif t == "upload_file":
            # Create a minimal test file if needed
            file_path = action.value or "test.txt"
            if not os.path.exists(file_path):
                try:
                    with open(file_path, "w") as f:
                        f.write("Test file content for upload testing.")
                except Exception:
                    pass
            return await self.engine.upload_file(action.selector, file_path)

        elif t == "drag_drop":
            return await self.engine.drag_and_drop(action.selector, action.target_selector)

        else:
            return {"success": False, "error": f"Unknown action type: {t}"}

    async def _capture_page_state(self) -> PageState:
        """Capture complete page state including DOM extraction and visual checks."""
        extractor = Extractor(self.engine.page)

        raw = await extractor.extract_page_state()
        visual_issues = await extractor.check_visual_issues()
        errors = self.observer.get_errors_summary()

        return PageState(
            url=raw.get("url", await self.engine.current_url()),
            title=raw.get("title", ""),
            visible_text_summary=raw.get("visible_text_summary", ""),
            headings=raw.get("headings", []),
            links=raw.get("links", []),
            buttons=raw.get("buttons", []),
            inputs=raw.get("inputs", []),
            selects=raw.get("selects", []),
            forms=raw.get("forms", []),
            images=raw.get("images", []),
            errors_summary=errors,
            visual_issues=visual_issues,
        )

    async def _handle_login(self, url: str, auth: dict):
        """Navigate to the URL and attempt to log in."""
        self._status("auth", f"Navigating to {url} for login...")
        result = await self.engine.goto(url)
        if not result["success"]:
            self._status("error", f"Cannot load login page: {url}")
            return

        await self.engine.wait_for_stable()

        state = await self._capture_page_state()

        self._status("auth", "Identifying login form...")
        login_info = self.planner.identify_login(state)

        if not login_info.get("is_login_page", False):
            self._status("auth", "No login form detected — proceeding without auth")
            self._step_log = [f"1. Navigate to {url}"]
            self._global_step_num = 1
            return

        username_sel = login_info.get("username_selector", "")
        password_sel = login_info.get("password_selector", "")
        submit_sel = login_info.get("submit_selector", "")

        if not username_sel or not password_sel or not submit_sel:
            self._status("error", "Could not identify all login form fields")
            self._step_log = [f"1. Navigate to {url}"]
            self._global_step_num = 1
            return

        self._status("auth", "Filling login form...")
        await self.engine.fill(username_sel, auth["username"])
        await self.engine.fill(password_sel, auth["password"])

        self._status("auth", "Submitting login...")
        await self.engine.click(submit_sel)
        await self.engine.wait_for_stable()
        await asyncio.sleep(2)

        new_url = await self.engine.current_url()
        new_title = await self.engine.current_title()
        self._status("auth", f"After login: {new_title} ({new_url})")

        self._step_log = [
            "1. Log in with valid credentials",
            f"2. Arrive at: {new_title}",
        ]
        self._global_step_num = 2

    def _enqueue_links(self, state: PageState):
        """Add discovered links to the exploration queue, scored by priority."""
        new_entries = []
        for link in state.links:
            href = link.get("href", "")
            if not href:
                continue
            norm = self._normalize(href)
            if norm in self._visited_urls:
                continue
            if self.same_origin_only and not self._is_same_origin(href):
                continue
            if norm in self._queued_norms:
                continue

            # Template dedup: if we already have 2+ URLs with the same path prefix,
            # skip further siblings (e.g., /download/file1.txt, /download/file2.txt)
            template = self._get_path_template(href)
            sibling_count = sum(1 for _, u in self._url_queue if self._get_path_template(u) == template)
            if sibling_count >= 2:
                continue

            link_text = link.get("text", "")
            score = _score_url(href, link_text, self._focus_area)
            new_entries.append((score, href))
            self._queued_norms.add(norm)

        if new_entries:
            self._url_queue.extend(new_entries)
            # Sort descending by score so pop(0) gets highest priority
            self._url_queue.sort(key=lambda x: x[0], reverse=True)

    def _get_path_template(self, url: str) -> str:
        """Collapse a URL path into a template for dedup. /download/file1.txt -> /download/*"""
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) <= 1:
            return parsed.path
        # Keep everything except the last segment (the variant)
        return "/" + "/".join(parts[:-1]) + "/*"

    def _normalize(self, url: str) -> str:
        url = urldefrag(url)[0].rstrip("/")
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def _is_same_origin(self, url: str) -> bool:
        return urlparse(url).netloc == self._origin

    def _status(self, category: str, message: str):
        self._on_status(category, message)
