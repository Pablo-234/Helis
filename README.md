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
                    VALIDATED
                         ↓
             PLAN → BUILD → VERIFY
                         ↓
                TESTED LOCAL MVP
```

## What works now

HELIS can:

- collect traceable market observations from RSS/Atom, public GitHub issues and Hacker News,
- generate venture candidates only when they reference supplied observations,
- score candidates with deterministic, inspectable arithmetic,
- challenge promising candidates with a skeptic pass,
- design cheap falsification experiments,
- execute zero-cash desk research against its real observation corpus,
- autonomously plan one follow-up test when evidence is insufficient,
- make deterministic **advance / continue / pivot / kill** decisions,
- require multiple independent positive experiment types before a venture becomes `validated`,
- dispatch approved interview/pricing experiments through a separately configured HTTPS validation gateway,
- keep external dispatch asynchronous and idempotent,
- enforce model-call, token, cash and duration limits,
- convert a validated venture into a bounded MVP BuildSpec,
- generate a file-only MVP bundle under path/type/size allowlists,
- write each build into an isolated per-venture/per-run workspace,
- hash every generated file and persist a bundle digest,
- verify static web builds without executing their JavaScript,
- verify Python stdlib builds only inside a fixed Docker sandbox with networking disabled,
- refuse to run generated Python on the host if Docker or the sandbox image is unavailable,
- persist build state and append build events to the audit trail.

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

When a venture reaches `validated`, the builder can pick it up without a manually supplied product idea:

```bash
helis build
helis build-status
```

`helis build` plans the smallest MVP, generates only allowed files, creates an isolated workspace and runs the fixed verifier for the selected runtime. It does **not** deploy the product.

## Approved external validation gateway

HELIS never lets the model choose a customer-contact endpoint or credentials. The operator can optionally configure one HTTPS gateway:

```bash
HELIS_VALIDATION_GATEWAY_URL=https://your-gateway.example/helis/validation
HELIS_VALIDATION_GATEWAY_TOKEN=...
helis gateway-status
```

The gateway can be a narrow service, n8n/Make workflow, or another controlled integration that performs the actual interview/pricing action. HELIS sends the persisted run ID as an idempotency key and waits asynchronously for a real result.

```text
planned → waiting_approval → ready → running → waiting_result → completed
```

`approve-run` approves exactly one persisted run. It does **not** weaken the global policy or approve future experiments.

## Builder safety

The model never chooses a shell command. Builder v0 accepts only two runtimes:

- `static_web` — local HTML/CSS/JS bundle, verified offline without JavaScript execution.
- `python_stdlib` — Python standard library only, with `unittest` tests executed by a fixed command inside Docker.

Python sandbox verification uses a read-only container, no network, all capabilities dropped, `no-new-privileges`, PID/memory/CPU limits, an unprivileged user and a read-only workspace mount. HELIS does not auto-pull images during a build and has no host-execution fallback.

Generated files cannot escape their workspace and cannot include `.env`, `.git`, `.github`, `.ssh`, package-install manifests, binary/NUL content, unsupported extensions or arbitrary file counts/sizes. See `docs/BUILDER.md`.

## Decision safety

The model can summarize evidence and propose tests. It does **not** own the final venture transition.

- **KILL**: one negative result with confidence >= 0.88, or accumulated negative confidence >= 1.3.
- **ADVANCE**: positive confidence weight >= 1.4 across at least two independent experiment types, with negative weight < 0.5.
- **PIVOT**: credible adjacent evidence with confidence >= 0.6 while the current hypothesis remains weak.
- **CONTINUE**: not enough evidence yet; HELIS may design one next information-gaining test.

ADVANCE means `validated`. The Builder separately claims the venture into `building`.

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

HELIS can now progress from market observations to a **validated venture and a locally tested MVP workspace**. It still does not autonomously publish/deploy that MVP to the public internet, access production credentials, install arbitrary packages, or transact money.

The next Phase 2 slice is bounded repair/self-review, followed by a separately gated ephemeral preview deployment.

See `docs/ROADMAP.md`, `docs/VALIDATION.md`, and `docs/BUILDER.md`.
