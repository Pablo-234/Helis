# HELIS 🧬

**HELIS is an autonomous venture engine.**

Its job is not to wait for a business idea. Its job is to continuously discover problems and market inefficiencies, turn them into testable venture hypotheses, validate them as cheaply as possible, build only what evidence justifies, launch controlled experiments, measure outcomes, and decide whether to **scale, pivot, pause, or kill**.

> HELIS does not wait for the owner to invent businesses. HELIS discovers them.

## Core loop

```text
OBSERVE
  ↓
DISCOVER
  ↓
HYPOTHESIZE
  ↓
EVALUATE
  ↓
FALSIFY
  ↓
VALIDATE
  ↓
BUILD
  ↓
LAUNCH
  ↓
MEASURE
  ↓
SCALE / PIVOT / PAUSE / KILL
  ↺
```

## What works now

HELIS v0 can already:

- ingest traceable market observations from configured sources,
- scan RSS/Atom feeds, public GitHub issues and Hacker News feeds,
- use an OpenAI-compatible model endpoint (including local endpoints),
- generate venture candidates only when they reference supplied observations,
- score candidates with transparent deterministic arithmetic,
- challenge the best viable candidate with a skeptic pass,
- identify high-risk assumptions and missing evidence,
- design cheap falsification experiments,
- rank experiments by information value vs effort/cash,
- gate spending, external contact and publication outside the LLM prompt,
- persist state and append-only audit events in SQLite,
- enforce model-call, token and configured-cost budgets per cycle.

## Quick start

Python 3.11+ is required.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

HELIS defaults to an OpenAI-compatible local endpoint at `http://localhost:11434/v1` and model `qwen3.5:9b`. Override with:

```bash
HELIS_LLM_BASE_URL=...
HELIS_LLM_MODEL=...
HELIS_LLM_API_KEY=...
```

On PowerShell use `$env:HELIS_LLM_MODEL="..."` etc.

Then run one full bounded cycle:

```bash
helis run
```

The checked-in `helis.toml` starts with Hacker News Ask/Show feeds. Edit the config to add RSS feeds or public GitHub issue streams relevant to markets HELIS should inspect.

## Design principles

1. **Evidence before effort** — cheap tests before expensive builds.
2. **Kill ideas freely** — sunk-cost attachment is a bug.
3. **Budget is a hard constraint** — every action has a cost envelope.
4. **Autonomy is permissioned** — research can be broad; money, public actions, credentials and irreversible effects are separately gated.
5. **Everything is auditable** — decisions and state transitions are recorded as append-only events.
6. **Framework-independent core** — LLMs and agent frameworks are adapters, not the architecture.
7. **No silent self-modification** — changes to HELIS itself must go through tests and version control.
8. **Optimize expected value, not activity** — more agents, tokens or projects are not success metrics.

## Current boundary

HELIS can **discover, evaluate, falsify and plan validation**. It does not yet autonomously execute customer outreach, paid experiments, product deployment or financial transactions. Those capabilities will be added behind explicit policy and budget gates.

See `docs/ROADMAP.md` for the staged path from the current business brain to validation, building, go-to-market and portfolio capital allocation.
