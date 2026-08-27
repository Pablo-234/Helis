# HELIS 🧬

**HELIS is an autonomous venture engine.**

Its job is not to wait for a business idea. It continuously discovers problems and market inefficiencies, turns them into testable venture hypotheses, validates them as cheaply as possible, builds only what evidence justifies, launches controlled experiments, measures outcomes, and decides whether to **advance, pivot, continue, or kill**.

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
- make deterministic **advance / continue / pivot / kill** decisions,
- require multiple independent positive experiment types before a venture becomes `validated`,
- kill strongly falsified ventures before product development,
- dispatch approved interview/pricing experiments through a separately configured HTTPS validation gateway,
- keep external dispatch asynchronous with a persisted `waiting_result` state,
- prevent duplicate external sends with the run ID as an idempotency key,
- require explicit approval on every gateway-backed run even if a model incorrectly says contact is unnecessary,
- enforce model-call, token, cash and duration limits,
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

## Approved external validation gateway

HELIS never lets the model choose a customer-contact endpoint or credentials. The operator can optionally configure one HTTPS gateway:

```bash
HELIS_VALIDATION_GATEWAY_URL=https://your-gateway.example/helis/validation
HELIS_VALIDATION_GATEWAY_TOKEN=...
helis gateway-status
```

The gateway can be a narrow service, n8n/Make workflow, or another controlled integration that performs the actual interview/pricing action. HELIS sends a versioned JSON payload containing the approved run, experiment, venture and hard cost/duration constraints. It also sends the `ExperimentRun.id` as an `Idempotency-Key`.

A customer-facing run follows this flow:

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

The gateway destination must use HTTPS. Plain HTTP is accepted only for localhost development when `HELIS_ALLOW_INSECURE_LOCAL_GATEWAY=1` is explicitly set.

Useful commands:

```bash
helis validate --validation-cash-cents 0
helis approve-run <RUN_ID>
helis record-result result.json
helis gateway-status
helis rank
```

`approve-run` approves exactly one persisted run. It does **not** weaken the global policy or approve future experiments.

## Decision safety

The model can summarize evidence and propose tests. It does **not** own the final venture transition.

- **KILL**: one negative result with confidence >= 0.88, or accumulated negative confidence >= 1.3.
- **ADVANCE**: positive confidence weight >= 1.4 across at least two independent experiment types, with negative weight < 0.5.
- **PIVOT**: credible adjacent evidence with confidence >= 0.6 while the current hypothesis remains weak.
- **CONTINUE**: not enough evidence yet; HELIS may design one next information-gaining test.

ADVANCE means `validated`, not `building`. Product construction is a separate capability boundary.

## Design principles

1. **Evidence before effort** — cheap tests before expensive builds.
2. **Kill ideas freely** — sunk-cost attachment is a bug.
3. **Budget is a hard constraint** — every action has a cost envelope.
4. **Autonomy is permissioned** — research can be broad; external side effects are separately gated.
5. **Everything is auditable** — important state transitions append events.
6. **Framework-independent core** — models and agent frameworks are adapters, not the architecture.
7. **No silent self-modification** — changes to HELIS itself go through git and tests.
8. **Optimize expected value, not activity** — thinking more is not success.

## Current boundary

HELIS can now perform its validation loop and has a controlled external execution bridge. Native channel integrations (direct email/SMS/social/forms), autonomous deployment, financial transactions and product building are still separate future capabilities.

See `docs/ROADMAP.md` and `docs/VALIDATION.md`.
