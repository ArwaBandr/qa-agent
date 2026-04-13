"""
Config loader — reads YAML config with sensible defaults.
"""

import os
import yaml


DEFAULTS = {
    "llm": {
        "provider": "claude",
        "model": "claude-sonnet-4-6",
        "api_key": "",
    },
    "browser": {
        "headless": True,
        "viewport_width": 1920,
        "viewport_height": 1080,
        "timeout": 30000,
        "action_timeout": 10000,
    },
    "exploration": {
        "max_pages": 200,
        "same_origin_only": True,
        "focus": "",
    },
    "auth": {
        "username": "",
        "password": "",
    },
    "artifacts": {
        "dir": "./artifacts",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, recursively for nested dicts."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: str = None) -> dict:
    """Load config from YAML file, merged with defaults."""
    config = DEFAULTS.copy()

    if config_path and os.path.exists(config_path):
        with open(config_path, "r") as f:
            user_config = yaml.safe_load(f) or {}
        config = _deep_merge(config, user_config)

    # Allow env var override for API key
    if not config["llm"]["api_key"]:
        provider = config["llm"].get("provider", "claude")
        # Check provider-specific env vars, then generic one
        env_keys = {
            "claude": ["ANTHROPIC_API_KEY", "EXPLORER_LLM_API_KEY"],
            "openai": ["OPENAI_API_KEY", "EXPLORER_LLM_API_KEY"],
            "ollama": [],
        }
        for var in env_keys.get(provider, ["EXPLORER_LLM_API_KEY"]):
            val = os.environ.get(var)
            if val:
                config["llm"]["api_key"] = val
                break

    return config
