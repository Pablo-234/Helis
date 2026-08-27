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
PLAN EXPERIMENT
  ↓
EXECUTE
  ↓
MEASURE
  ↓
ADVANCE / CONTINUE / PIVOT / KILL
  ↓
BUILD (only after validation)
```

## What works now

HELIS can:

- ingest traceable market observations from configured sources,
- scan RSS/Atom feeds, public GitHub issues and Hacker News feeds,
- generate venture candidates only when they reference supplied observations,
- score candidates with transparent deterministic arithmetic,
- challenge the best viable candidate with a skeptic pass,
- design and rank cheap falsification experiments,
- persist every experiment execution as a resumable state machine,
- autonomously execute zero-cash desk-research experiments against real collected observations,
- reject model conclusions that cite observation IDs not present in the research corpus,
- persist validation results and actual configured model cost,
- make deterministic **advance / continue / pivot / kill** decisions from validation evidence,
- require multiple independent positive experiment types before advancing to the builder,
- kill a venture when strong falsifying evidence crosses a transparent threshold,
- gate spending, customer contact and publication outside the LLM prompt,
- grant approval to one specific experiment run without weakening the global policy,
- persist append-only audit events in SQLite,
- enforce model-call, token and configured-cost budgets.

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

Then run the autonomous bounded loop:

```bash
helis run
```

`helis run` now performs a market scan, discovery/evaluation/falsification, experiment planning, and **one safe validation execution step**. The default validation cash budget is zero, so the built-in desk-research adapter can run but paid/public/customer-contact experiments cannot silently escape the policy gate.

Useful control commands:

```bash
helis validate                 # execute one pending safe validation step
helis approve-run <RUN_ID>     # one-time approval for one waiting run
helis record-result result.json # ingest a result from an external adapter
helis rank
```

## Decision safety

A model may summarize observations and propose evidence. It does **not** own the final venture decision. HELIS computes the transition using explicit rules:

- one very strong falsifier (confidence >= 0.88), or enough accumulated negative weight, can kill;
- advancing requires positive weight >= 1.4 across at least **two independent experiment types**;
- a credible adjacent signal can recommend a pivot;
- otherwise the venture stays in validation.

These thresholds are deliberately inspectable and testable.

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

HELIS now executes **desk-research validation** autonomously. Interview, pricing, smoke-test and concierge experiments have execution states and approval gates but do not yet have live outreach/publication adapters. HELIS therefore cannot silently contact customers or publish assets.

See `docs/ROADMAP.md` for the staged path to full validation, building, go-to-market and portfolio capital allocation.
