# HELIS 🧬

**HELIS is an autonomous venture engine.**

Its job is not to wait for a business idea. It continuously discovers problems and market inefficiencies, turns them into testable venture hypotheses, validates them as cheaply as possible, builds only what evidence justifies, launches controlled go-to-market work, measures outcomes, reallocates scarce resources, and decides whether to **advance, continue, pivot, pause, scale, or kill**.

> HELIS does not wait for the owner to invent businesses. HELIS discovers them.

## Core loop

```text
OBSERVE → DISCOVER → HYPOTHESIZE → EVALUATE → FALSIFY
                         ↓
                  PLAN EXPERIMENT
                         ↓
                      EXECUTE
                         ↓
                      MEASURE
                         ↓
            ADVANCE / CONTINUE / PIVOT / KILL
                         ↓
                 BUILD if validated
                         ↓
                PREVIEW / LAUNCH
                         ↓
             GTM → RESPONSE → REVENUE
                         ↓
              PORTFOLIO REALLOCATION
```

## What works now

HELIS can:

- collect traceable market observations from RSS/Atom, public GitHub issues and Hacker News,
- generate venture candidates only when they reference supplied observations,
- score candidates with deterministic, inspectable arithmetic,
- challenge promising candidates with a skeptic pass,
- design cheap falsification experiments,
- persist experiment execution as a resumable state machine,
- execute zero-cash desk research against its real observation corpus,
- reject model conclusions that cite evidence IDs it did not actually receive,
- autonomously plan one follow-up test when evidence is insufficient,
- make deterministic validation **advance / continue / pivot / kill** decisions,
- require multiple independent positive experiment types before a venture becomes `validated`,
- kill strongly falsified ventures before product development,
- build constrained `static_web_v1` and `concierge_ops_v1` artifacts in isolated workspaces,
- run deterministic build checks plus adversarial model review,
- perform one bounded automatic repair attempt by default,
- hash-lock reviewed preview bytes before approved publication,
- discover B2B prospects through evidence-bound public signals,
- qualify leads and draft first-contact outreach without fabricated personalization,
- enforce tiny contact batches, identity limits, suppression/opt-out state and run-scoped approval,
- automatically dispatch only outreach runs that were already explicitly approved,
- ingest GTM responses, attribute revenue and derive deterministic **continue / pause / kill / scale** decisions,
- estimate per-venture economics with currency-separated revenue/cost accounting,
- allocate shared cash/model capacity across competing ventures,
- enforce persistent per-venture resource envelopes,
- reserve/settle/release cash commitments without silently minting spent capacity back,
- automatically roll only remaining treasury into new portfolio plans after material GTM/economics changes,
- select the next eligible funded venture with a bounded portfolio scheduler,
- wake safely from cron/systemd using throttling plus an expiring singleton lease,
- apply adaptive per-venture GTM cooldowns when repeated wakes cannot make progress,
- retain an append-only audit trail in SQLite.

## Quick start

Python 3.11+ is required.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

HELIS defaults to an OpenAI-compatible local endpoint at `http://localhost:11434/v1` and model `qwen3.5:9b`.

```bash
HELIS_LLM_BASE_URL=...
HELIS_LLM_MODEL=...
HELIS_LLM_API_KEY=...
```

Then:

```bash
helis run
```

`helis run` scans markets, performs discovery/evaluation/falsification, plans experiments and executes one safe validation step. The default validation cash budget is **zero**.

For the portfolio control loop:

```bash
helis-scheduler health
helis-scheduler wake
helis-scheduler wake-status
helis-scheduler status
```

`wake` is safe to invoke frequently from cron/systemd. HELIS itself enforces the minimum wake interval, single-worker lease, per-tick work cap, resource envelopes, approvals and adaptive venture cooldowns.

## Approved external gateways

HELIS never lets a model choose transport destinations or credentials. External boundaries are separately operator-configured:

- validation gateway — approved interview/pricing validation transport,
- preview gateway — approved publication of the exact reviewed artifact hash,
- prospect gateway — read-only B2B prospect discovery,
- contact gateway — one already-approved first contact.

Example validation configuration:

```bash
HELIS_VALIDATION_GATEWAY_URL=https://your-gateway.example/helis/validation
HELIS_VALIDATION_GATEWAY_TOKEN=...
helis gateway-status
```

The gateway can be a narrow service, n8n/Make workflow, or another controlled integration. Customer-facing writes remain idempotent and approval-scoped.

A validation run follows this flow:

```text
planned
  ↓
waiting_approval
  ↓  helis approve-run <RUN_ID>
ready
  ↓  helis validate
running
  ↓ gateway accepts dispatch
waiting_result
  ↓ result arrives
completed
  ↓
advance / continue / pivot / kill
```

A GTM first contact similarly requires a persisted approved outreach run before the contact gateway can be called. The scheduler can prepare work for approval, but cannot grant that approval to itself.

Gateway destinations must use HTTPS. Plain HTTP is accepted only for explicit localhost development opt-ins.

## Decision safety

The model can summarize evidence, propose tests, generate bounded artifacts and draft outreach. It does **not** own final venture transitions or authorization boundaries.

Validation decisions are deterministic outside the model. GTM decisions are also derived from persisted outcomes and revenue rather than model preference. Portfolio allocation then uses those measured signals and explicit economics to assign only remaining cash/model capacity.

## Design principles

1. **Evidence before effort** — cheap tests before expensive builds.
2. **Kill ideas freely** — sunk-cost attachment is a bug.
3. **Budget is a hard constraint** — every action has a persistent resource envelope.
4. **Autonomy is permissioned** — research can be broad; external side effects are separately gated.
5. **Everything is auditable** — important state transitions append events.
6. **Framework-independent core** — models and agent frameworks are adapters, not the architecture.
7. **No silent self-modification** — changes to HELIS itself go through git, tests and explicit merge policy.
8. **Optimize expected value, not activity** — repeated reads/no-op wakes are not success.
9. **Durable state beats resident agents** — HELIS can reconstruct its control loop after a crash or reboot.

## Current boundary

HELIS now covers the constrained path from market observation through validation, MVP artifact building, bounded B2B GTM, measured revenue/economics and portfolio scheduling/reallocation.

Still intentionally separate or incomplete:

- native direct email/SMS/social channel implementations,
- automatic multi-channel acquisition experimentation,
- automatic pricing experimentation,
- general arbitrary executable-code builders,
- direct payment authority,
- silent production deployment,
- controlled self-improvement/patch proposal and eval/merge pipeline.

## Running continuously

Reference Linux systemd and cron deployment assets live in `deploy/`. The scheduler is intentionally host-woken rather than an unbounded resident `while True` process.

See:

- `docs/OPERATIONS.md` — systemd/cron installation, health checks, restart behavior and logs,
- `docs/ROADMAP.md` — capability roadmap,
- `docs/VALIDATION.md` — validation execution model.
