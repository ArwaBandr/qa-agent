# QA Explorer

AI-powered exploratory testing agent. Give it a URL, it finds bugs.

Uses an LLM (Claude or GPT) + Playwright to autonomously explore web applications like a manual QA tester — understanding pages, interacting with forms and buttons, following multi-step flows, and reporting functional and visual bugs.

## Install (Team Members)

You received a `.whl` file — that's all you need. No source code required.

**Prerequisites:** Python 3.9+ installed on your machine.

**Step 1 — Install:**

```bash
pip install qa_explorer-0.1.0-py3-none-any.whl
playwright install chromium
```

**Step 2 — Generate config:**

```bash
qa-explorer --init
```

This creates a `config.yaml` in your current directory.

**Step 3 — Edit config.yaml** with your provider, model, and API key:

```yaml
llm:
  provider: "openai"                          # or claude, ollama, local
  model: "your-model-name"
  api_key: "your-api-key"
  base_url: "https://your-local-server/v1"    # only if using a custom endpoint
```

**Step 4 — Run:**

```bash
qa-explorer https://your-app.com --config config.yaml
```

## Install (From Source)

If you have access to the source code:

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

# Use a local model (LM Studio, vLLM, LocalAI, etc.)
qa-explorer https://myapp.com --provider local --model my-model
qa-explorer https://myapp.com --provider local --model my-model --base-url http://localhost:1234/v1

# Use Ollama
qa-explorer https://myapp.com --provider ollama --model llama3

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
| `--provider` | LLM provider: `claude`, `openai`, `ollama`, `local` |
| `--model` | LLM model name |
| `--api-key` | LLM API key (or use env var) |
| `--base-url` | Custom API endpoint (for local servers, proxies) |
| `--fast-model` | Cheaper/faster model for simple tasks |
| `--focus` | Focus testing on a specific area |
| `--max-pages` | Max pages to explore (default: 200) |
| `--headless` / `--no-headless` | Run browser headless or visible |

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit:

```yaml
llm:
  provider: "claude"           # claude | openai | ollama | local
  model: "claude-sonnet-4-6"
  api_key: ""                  # or set ANTHROPIC_API_KEY env var
  base_url: ""                 # custom endpoint (for local servers)
  fast_model: ""               # cheaper model for simple tasks

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
| Local | (none needed) | `--base-url http://localhost:1234/v1` |

## How It Works

1. **Page Analysis** — Reads the page structure, forms, buttons, links
2. **Priority Exploration** — Scores URLs by likely bug density, explores high-value pages first
3. **Multi-Step Flows** — Discovers and executes end-to-end user flows (e.g., add to cart -> checkout -> confirm)
4. **Bug Detection** — An LLM judge evaluates each action's result for functional bugs, visual issues, and broken behavior
5. **Smart Recovery** — Detects stuck loops, auto-reports blocked flows as bugs, moves on

## Sharing with Your Team

To distribute without sharing source code:

```bash
# Build the wheel (run once from the source directory)
pip install build
python -m build --wheel
```

This creates `dist/qa_explorer-0.1.0-py3-none-any.whl` (~39KB). Share this single file with your team via Slack, shared drive, email, etc.

Each team member installs with:

```bash
pip install qa_explorer-0.1.0-py3-none-any.whl
playwright install chromium
```

To update the tool, rebuild the wheel (bump version in `pyproject.toml`) and have the team run:

```bash
pip install --force-reinstall qa_explorer-0.2.0-py3-none-any.whl
```

## Requirements

- Python 3.9+
- A supported LLM API key (Claude, OpenAI, or compatible) or local model
