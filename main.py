"""
Exploratory Testing Agent — CLI entry point.

Usage:
  qa-explorer <url> [options]
  qa-explorer https://example.com --config config.yaml
  qa-explorer https://example.com --username admin --password secret
  qa-explorer https://example.com --provider openai --model gpt-4o
"""

import argparse
import asyncio
import sys
import os
import time

# Support running both as `python main.py` and as installed `explorer` command
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import load_config
from agent.brain import LLMClient
from agent.explorer import Explorer
from browser.engine import BrowserEngine
from models.bug import BugReport


# ── CLI Output Formatting ──────────────────────────────────────

class CLIReporter:
    """Handles all CLI output: status updates, bug reports, summary."""

    def __init__(self):
        self.bugs: list[BugReport] = []
        self._start_time = time.time()
        self._action_count = 0

    def on_status(self, category: str, message: str):
        icons = {
            "navigate":   "[NAV]",
            "analyze":    "[PAGE]",
            "understand": "[AI]",
            "observe":    "[AI]",
            "strategy":   "[PLAN]",
            "action":     "[DO]",
            "auth":       "[AUTH]",
            "bug":        "[BUG!]",
            "warning":    "[WARN]",
            "error":      "[ERR]",
        }
        icon = icons.get(category, f"[{category.upper()}]")

        if category == "bug":
            print(f"\n  \033[91m{icon} {message}\033[0m")
        elif category == "error":
            print(f"  \033[91m{icon} {message}\033[0m")
        elif category == "warning":
            print(f"  \033[93m{icon} {message}\033[0m")
        elif category == "understand":
            print(f"  \033[96m{icon} {message}\033[0m")
        elif category == "action":
            self._action_count += 1
            print(f"  \033[92m{icon} {message}\033[0m")
        elif category == "navigate":
            print(f"  {icon} {message}")
        else:
            print(f"  {icon} {message}")

    def on_bug(self, bug: BugReport):
        self.bugs.append(bug)

    def print_summary(self):
        elapsed = time.time() - self._start_time
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)

        print()
        print("  " + "=" * 60)
        print("  EXPLORATION COMPLETE")
        print("  " + "=" * 60)
        print(f"  Duration: {minutes}m {seconds}s")
        print(f"  Actions performed: {self._action_count}")
        print(f"  Bugs found: {len(self.bugs)}")
        print("  " + "=" * 60)

        if not self.bugs:
            print()
            print("  No bugs found. The application appears to be working correctly.")
            print()
        else:
            # Group by severity
            by_severity = {"critical": [], "high": [], "medium": [], "low": []}
            for bug in self.bugs:
                by_severity.get(bug.severity, by_severity["medium"]).append(bug)

            bug_num = 0
            for severity in ("critical", "high", "medium", "low"):
                bugs = by_severity[severity]
                if not bugs:
                    continue
                print()
                print(f"  --- {severity.upper()} ({len(bugs)}) ---")
                for bug in bugs:
                    bug_num += 1
                    print()
                    print(f"  BUG #{bug_num}")
                    print(bug.to_cli_output())

            print()


# ── Main ───────────────────────────────────────────────────────

def print_banner():
    print()
    print("  ============================================")
    print("  |     Exploratory Testing Agent            |")
    print("  |     AI-Powered Bug Hunter                |")
    print("  ============================================")
    print()


async def run_exploration(url: str, config: dict):
    browser_cfg = config["browser"]
    explore_cfg = config["exploration"]
    llm_cfg = config["llm"]
    auth_cfg = config["auth"]

    # Init reporter
    reporter = CLIReporter()

    # Init LLM
    print(f"  LLM: {llm_cfg['provider']} / {llm_cfg['model']}")
    print(f"  Target: {url}")
    print(f"  Max pages: {explore_cfg['max_pages']}")
    print(f"  Headless: {browser_cfg['headless']}")
    if explore_cfg.get("focus"):
        print(f"  Focus: {explore_cfg['focus']}")
    if auth_cfg.get("username"):
        print(f"  Auth: {auth_cfg['username']} / {'*' * len(auth_cfg.get('password', ''))}")
    print()

    llm = LLMClient(
        provider=llm_cfg["provider"],
        model=llm_cfg["model"],
        api_key=llm_cfg["api_key"],
        base_url=llm_cfg.get("base_url", ""),
        fast_model=llm_cfg.get("fast_model", ""),
    )

    # Init browser
    engine = BrowserEngine(
        headless=browser_cfg["headless"],
        viewport_width=browser_cfg["viewport_width"],
        viewport_height=browser_cfg["viewport_height"],
        timeout=browser_cfg["timeout"],
        action_timeout=browser_cfg["action_timeout"],
    )

    print("  Starting browser...")
    await engine.start()

    try:
        explorer = Explorer(
            engine=engine,
            llm=llm,
            max_pages=explore_cfg["max_pages"],
            same_origin_only=explore_cfg["same_origin_only"],
            focus_area=explore_cfg.get("focus", ""),
            on_status=reporter.on_status,
            on_bug=reporter.on_bug,
        )

        auth = None
        if auth_cfg.get("username") and auth_cfg.get("password"):
            auth = {"username": auth_cfg["username"], "password": auth_cfg["password"]}

        print("  Starting exploration...\n")
        bugs = await explorer.run(url, auth=auth)

        reporter.print_summary()

    except KeyboardInterrupt:
        print("\n\n  Exploration interrupted by user.")
        reporter.print_summary()
    finally:
        await engine.stop()


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="Exploratory Testing Agent - AI-Powered Bug Hunter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  qa-explorer https://example.com
  qa-explorer https://myapp.com --username admin --password secret
  qa-explorer https://myapp.com --provider openai --model gpt-4o
  qa-explorer https://myapp.com --config config.yaml --no-headless
  qa-explorer https://myapp.com --max-pages 50
  qa-explorer https://myapp.com --focus "checkout flow"
        """,
    )
    parser.add_argument("url", help="URL of the system under test")
    parser.add_argument("--config", help="Path to config YAML file")
    parser.add_argument("--username", help="Login username")
    parser.add_argument("--password", help="Login password")
    parser.add_argument("--max-pages", type=int, help="Max pages to explore")
    parser.add_argument("--provider", help="LLM provider: claude, openai, ollama, local")
    parser.add_argument("--model", help="LLM model name")
    parser.add_argument("--api-key", help="LLM API key")
    parser.add_argument("--base-url", help="Custom API base URL (for local servers, proxies)")
    parser.add_argument("--fast-model", help="Cheaper/faster model for simple tasks")
    parser.add_argument("--focus", help="Focus testing on a specific area (e.g., 'checkout flow', 'search feature')")
    parser.add_argument("--headless", action="store_true", default=None)
    parser.add_argument("--no-headless", action="store_false", dest="headless")

    args = parser.parse_args()
    config = load_config(args.config)

    # CLI overrides
    if args.username:
        config["auth"]["username"] = args.username
    if args.password:
        config["auth"]["password"] = args.password
    if args.max_pages:
        config["exploration"]["max_pages"] = args.max_pages
    if args.provider:
        config["llm"]["provider"] = args.provider
    if args.model:
        config["llm"]["model"] = args.model
    if args.api_key:
        config["llm"]["api_key"] = args.api_key
    if args.base_url:
        config["llm"]["base_url"] = args.base_url
    if args.fast_model:
        config["llm"]["fast_model"] = args.fast_model
    if args.focus:
        config["exploration"]["focus"] = args.focus
    if args.headless is not None:
        config["browser"]["headless"] = args.headless

    # Validate API key (not needed for ollama or local providers)
    if not config["llm"]["api_key"] and config["llm"]["provider"] not in ("ollama", "local"):
        provider = config["llm"]["provider"]
        env_var = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider, "EXPLORER_LLM_API_KEY")
        print(f"  ERROR: No API key provided for {provider}.")
        print(f"  Set one of:")
        print(f"    export {env_var}=your-key-here")
        print(f"    qa-explorer <url> --api-key your-key-here")
        print(f"    config.yaml: llm.api_key: your-key-here")
        print()
        sys.exit(1)

    asyncio.run(run_exploration(args.url, config))


if __name__ == "__main__":
    main()
