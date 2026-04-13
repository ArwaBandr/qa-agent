# QA Explorer

AI-powered exploratory testing agent. Give it a URL, it finds bugs.

Uses an LLM (Claude or GPT) + Playwright to autonomously explore web applications like a manual QA tester — understanding pages, interacting with forms and buttons, following multi-step flows, and reporting functional and visual bugs.

## Install

```bash
pip install -e .
playwright install chromium
```

## Quick Start

```bash
# Set your API key
export ANTHROPIC_API_KEY=your-key-here
# or for OpenAI:
# export OPENAI_API_KEY=your-key-here

# Run
qa-explorer https://your-app.com
```

## Usage

```bash
# Basic
qa-explorer https://example.com

# With login credentials
qa-explorer https://myapp.com --username admin --password secret

# Use OpenAI instead of Claude
qa-explorer https://myapp.com --provider openai --model gpt-4o

# Focus on a specific area
qa-explorer https://myapp.com --focus "checkout flow"

# More pages, visible browser
qa-explorer https://myapp.com --max-pages 50 --no-headless

# Custom config file
qa-explorer https://myapp.com --config config.yaml
```

## Options

| Flag | Description |
|------|-------------|
| `--config` | Path to config YAML file |
| `--username` | Login username |
| `--password` | Login password |
| `--provider` | LLM provider: `claude`, `openai`, `ollama` |
| `--model` | LLM model name |
| `--api-key` | LLM API key (or use env var) |
| `--focus` | Focus testing on a specific area |
| `--max-pages` | Max pages to explore (default: 200) |
| `--headless` / `--no-headless` | Run browser headless or visible |

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit:

```yaml
llm:
  provider: "claude"
  model: "claude-sonnet-4-6"
  api_key: ""  # or set ANTHROPIC_API_KEY env var

browser:
  headless: true
  viewport_width: 1920
  viewport_height: 1080

exploration:
  max_pages: 200
  same_origin_only: true

auth:
  username: ""
  password: ""
```

CLI flags override config file values, which override defaults.

## API Key Setup

Set your key via environment variable (recommended) or CLI flag:

| Provider | Env Variable | CLI Flag |
|----------|-------------|----------|
| Claude | `ANTHROPIC_API_KEY` | `--api-key` |
| OpenAI | `OPENAI_API_KEY` | `--api-key` |
| Ollama | (none needed) | — |

## How It Works

1. **Page Analysis** — Reads the page structure, forms, buttons, links
2. **Priority Exploration** — Scores URLs by likely bug density, explores high-value pages first
3. **Multi-Step Flows** — Discovers and executes end-to-end user flows (e.g., add to cart -> checkout -> confirm)
4. **Bug Detection** — An LLM judge evaluates each action's result for functional bugs, visual issues, and broken behavior
5. **Smart Recovery** — Detects stuck loops, auto-reports blocked flows as bugs, moves on

## Requirements

- Python 3.10+
- A supported LLM API key (Claude or OpenAI) or local Ollama
