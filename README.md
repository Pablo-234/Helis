# HELIS 🧬

**HELIS is an autonomous venture engine and venture factory.**

Its job is not to wait for a business idea or become one hard-coded product. It continuously discovers problems and market inefficiencies, generates competing ways to monetize them, turns the strongest mechanisms into testable venture hypotheses, validates them as cheaply as possible, builds only what evidence justifies, launches controlled go-to-market work, measures outcomes, reallocates scarce resources, and decides whether to **advance, continue, pivot, pause, scale, or kill**.

> HELIS does not wait for the owner to invent businesses. HELIS discovers them — and it should eventually build the child agents needed to operate them.

## Core loop

```text
OBSERVE → DISCOVER PROBLEM → DIVERSIFY MONEY MODELS → EVALUATE → FALSIFY
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
- periodically wake market discovery from cron/systemd with an independent due interval and crash-safe lease,
- resume unprocessed discovery/evaluation work after crashes, retain signals when scouting produces
  no usable candidate, and replay stored evidence when an earlier empty result left the idea queue empty,
- generate venture candidates only when they reference supplied observations,
- run one focused hypothesis-recovery pass when the initial scout returns no usable online venture,
- turn one evidence-backed problem into several structurally different money-making models in the **same bounded discovery call**,
- represent who pays, what is sold, revenue model, delivery model, pricing hypothesis, acquisition wedge, fulfillment, automation roles, human roles, time-to-revenue, target owner effort and test cost explicitly,
- rank those money models with deterministic pre-validation arithmetic rather than accepting a model-awarded score,
- preserve different monetization strategies for the same pain as separate Opportunities while still merging true repeats,
- keep generated pricing/margin/time/effort economics explicitly classified as hypotheses rather than evidence,
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
- optionally build a dependency-free `python_service_v1` workflow core when an operator enables the executable sandbox,
- statically reject unsafe Python imports/introspection/top-level side effects before execution,
- execute only a fixed unittest command inside a non-root, read-only, resource-capped Docker sandbox with external networking disabled,
- require sandbox tests before executable code may become `verified`,
- preserve failed executable bytes and test output for the same bounded repair loop,
- run deterministic build checks plus adversarial model review,
- perform one bounded automatic repair attempt by default,
- hash-lock reviewed preview bytes before approved publication,
- discover B2B prospects through evidence-bound public signals,
- persist multiple public contact options for a lead while preserving the legacy primary endpoint,
- qualify leads and draft first-contact outreach without fabricated personalization,
- enforce tiny contact batches, identity limits, suppression/opt-out state and run-scoped approval,
- automatically dispatch only outreach runs that were already explicitly approved,
- automatically plan one bounded offer/pricing A/B experiment after real GTM feedback exists,
- assign control/variant commercial arms deterministically without increasing the existing contact cap,
- enforce explicit pricing-arm bounds and per-arm assignment/sample caps,
- hash-lock the selected commercial experiment arm into the approved outreach draft,
- automatically plan a bounded acquisition-channel experiment when a comparable dual-channel lead pool exists,
- assign public contact channels deterministically without a model call or extra outreach volume,
- hash-lock the exact selected channel and public endpoint into the approved draft,
- revalidate that endpoint immediately before dispatch and expose only that one endpoint to the contact gateway,
- calculate commercial and channel experiment winners deterministically from persisted replies, meetings, sales and revenue,
- ingest GTM responses, attribute revenue and derive deterministic **continue / pause / kill / scale** decisions,
- estimate per-venture economics with currency-separated revenue/cost accounting,
- allocate shared cash/model capacity across competing ventures,
- enforce persistent per-venture resource envelopes,
- reserve/settle/release cash commitments without silently minting spent capacity back,
- automatically roll only remaining treasury into new portfolio plans after material GTM/economics changes,
- select the next eligible funded venture with a bounded portfolio scheduler,
- wake portfolio execution safely from cron/systemd using throttling plus an expiring singleton lease,
- apply adaptive per-venture GTM cooldowns when repeated wakes cannot make progress,
- propose bounded low-authority improvements to HELIS itself in isolated hash-locked workspaces,
- compare exact baseline and candidate behavior before any git write,
- require explicit review-branch approval, exact green CI, a second merge approval and fresh pre-merge attestation before a self-improvement can reach the default branch,
- retain an append-only audit trail in SQLite,
- aggregate every pending side-effect approval and ready non-AI capability input in one operator inbox,
- require a fresh content-derived confirmation token for every approve/reject decision without executing the side effect itself,
- bootstrap a non-destructive local runtime and diagnose pilot blockers separately from optional live capabilities,
- run and persist a localhost-only, credential-free, zero-cash pilot with every external-write gateway omitted,
- materialize immutable venture-owned child agents from the current architecture/spec snapshot,
- execute dependent child-agent capabilities through a persistent venture-local DAG,
- pass only the initial venture input and completed dependency outputs into each child step,
- share one persisted model/token/cost ceiling across the complete child-agent graph,
- stop at explicit audited result gates for human, deterministic or external-service capabilities.

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
HELIS_LLM_REASONING_EFFORT=none
HELIS_LLM_TIMEOUT_SECONDS=300
HELIS_LLM_API_KEY=...
```

For the default localhost `qwen3.5` model, HELIS automatically uses `reasoning_effort=none` when
the setting is absent. This keeps bounded JSON calls from exhausting their output allowance on
reasoning before producing the required final object. Other models and remote providers retain
their own default unless this setting is explicitly configured. Normal calls allow 300 seconds by
default; `HELIS_LLM_TIMEOUT_SECONDS` can override that bound.

Then:

```bash
helis-live bootstrap
helis-live model-status
helis-live model-smoke
helis-live doctor --probe-model
helis-live pilot
helis-live pilot-status
```

`bootstrap` creates the local database, bounded workspaces and a safe public-source configuration without overwriting existing files. `model-status` distinguishes a missing runtime from a missing exact model and prints the next repair command. `model-smoke` makes one localhost-only completion capped at 96 output tokens, uses the same reasoning control as normal calls and verifies the required JSON contract. `doctor` distinguishes pilot blockers from optional production capabilities. `pilot` then uses the normal HELIS autopilot with a **localhost-only, credential-free, zero-priced model**, zero cash and every external-write gateway disabled. It may read configured public market sources and write local/audited state, but it cannot contact people, publish, create payment links, deploy or modify HELIS. `pilot-status` recovers the persisted report without network or model calls.

The readiness commands return nonzero when blocked, so automated launch scripts stop without
parsing console text. On Windows, `deploy/windows/Start-HelisControlledPilot.ps1` runs the sequence
above only after the operator supplies `-ConfirmPublicNetworkReads`; it does not install software,
download a model or register recurring tasks.

After the controlled pilot and live-adapter configuration, Windows can activate the complete
recurring path with one explicit command:

```powershell
.\deploy\windows\Start-HelisLive.ps1 -ConfirmLiveOperations
```

The launcher smoke-tests the local model, runs the external-write-disabled controlled pilot,
requires external validation plus all five preview/prospect/contact/reply/commerce adapter slots,
registers both tasks disabled, verifies their presence, then enables them. A failure leaves both
tasks disabled. It never grants validation, publication, first-contact or checkout approval.

The lower-level `helis run` command remains available for one discovery/evaluation/falsification and validation-planning step.

### Optional executable MVP sandbox

`python_service_v1` is disabled unless the operator explicitly enables Docker execution. Pre-pull the runtime image because HELIS uses `--pull never`; for stronger reproducibility, pin `HELIS_EXECUTABLE_SANDBOX_IMAGE` to a digest.

```bash
docker pull python:3.12-alpine
export HELIS_EXECUTABLE_SANDBOX=docker
export HELIS_EXECUTABLE_SANDBOX_IMAGE=python:3.12-alpine
```

Optional bounded controls:

```bash
HELIS_EXECUTABLE_SANDBOX_TIMEOUT=15
HELIS_EXECUTABLE_SANDBOX_MEMORY_MB=128
HELIS_EXECUTABLE_SANDBOX_CPUS=0.5
HELIS_EXECUTABLE_SANDBOX_PIDS=64
```

The model never supplies a shell command, image, Docker flags or test command. HELIS mounts only the generated run workspace read-only, disables external container networking with `--network none`, drops Linux capabilities, runs as UID/GID `65534`, sets `no-new-privileges`, caps memory/CPU/PIDs/time and executes one fixed `python -I -B -m unittest discover` command. This is a constrained test sandbox, **not** production deployment authority or a general arbitrary-code platform.

For continuous autonomous operation, the host wakes two independent bounded loops:

```bash
helis-discovery health
helis-discovery wake

helis-scheduler health
helis-scheduler wake
helis-scheduler wake-status
helis-scheduler status
```

`helis-discovery wake` scans configured sources and advances one resumable business-brain cycle. `helis-scheduler wake` advances funded venture execution. Both are safe to invoke more frequently than their actual work cadence because HELIS enforces independent persistent due intervals and singleton leases.

Review all unresolved operator work from one read-only queue:

```bash
helis-operator inbox
helis-operator show <KEY>
helis-operator approve <KEY> --confirm <TOKEN>
helis-operator reject <KEY> --confirm <TOKEN> --reason "<reason>"
```

Open the local owner dashboard to see, in one place, what HELIS has observed, which ventures it is
considering, their scores and stages, current validation/build/GTM progress, pending approvals,
recent audited activity and the names of generated workspace files:

```bash
helis-dashboard serve
```

The browser view binds only to `127.0.0.1`, refreshes every 30 seconds and is deliberately
read-only. It never exposes environment variables or file contents and cannot approve, publish,
contact or spend. Use `helis-dashboard snapshot` for the same credential-free state as JSON.

The 16-character token is derived from the current request contents. If the destination, message, offer, artifact or other reviewed input changes, the old command fails closed. Approval only moves the existing run to `ready`; the scheduler and its existing gateway policy still control execution.

GTM experiment state can be inspected without model or network calls:

```bash
helis-gtm experiments <OPPORTUNITY_ID>
helis-gtm channel-experiments <OPPORTUNITY_ID>
```

## Approved external gateways

HELIS never lets a model choose transport destinations or credentials. External boundaries are separately operator-configured:

- validation gateway — approved interview/pricing validation transport,
- preview gateway — approved publication of the exact reviewed artifact hash,
- prospect gateway — read-only B2B prospect discovery,
- contact gateway — one already-approved first contact to the exact approved public endpoint,
- self-evaluation gateway — isolated exact baseline-vs-candidate evaluation,
- self-branch gateway — writes only an explicitly approved candidate to its deterministic review branch,
- self-CI gateway — read-only exact review-branch CI attestation,
- self-merge gateway — performs only a second-approved base-locked merge.

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

A GTM first contact similarly requires a persisted approved outreach run before the contact gateway can be called. The scheduler can prepare work for approval, but cannot grant that approval to itself. Offer/pricing and channel experiments operate **inside** that same approval and contact-cap boundary: experiments may choose a bounded commercial arm or one already-public contact endpoint, but they never grant send authority, increase outreach volume, or give the gateway alternative destinations after approval.

Gateway destinations must use HTTPS. Plain HTTP is accepted only for explicit localhost development opt-ins.

## Decision safety

The model can summarize evidence, propose economic mechanisms, propose tests, generate bounded artifacts, draft outreach and propose a tightly bounded control/variant commercial experiment. It does **not** own final venture transitions or authorization boundaries. Channel experiment planning does not require a model at all. Executable build commands and isolation policy are also fixed outside the model.

Generated business-model economics are not evidence. The scout may hypothesize pricing, margins, time-to-revenue, acquisition paths and owner effort; the analyst and skeptic are explicitly instructed to treat them as claims that need validation. Initial model diversification is ranked by deterministic code and then exposed to the normal evidence/validation machinery.

Validation decisions are deterministic outside the model. GTM decisions are derived from persisted outcomes and revenue rather than model preference. Commercial and channel experiment assignment, sample caps, outcome scoring and winner selection are deterministic outside the model. Portfolio allocation then uses measured signals and explicit economics to assign only remaining cash/model capacity.

Self-improvement is also split across independent trust boundaries: proposal → isolated candidate → immutable evaluation → explicit branch approval → exact green CI → separate merge approval → fresh matching CI → base-locked merge. A stale approval, changed branch head or advanced default branch blocks the merge rather than rebasing or silently applying old code.

## Design principles

1. **Evidence before effort** — cheap tests before expensive builds.
2. **Kill ideas freely** — sunk-cost attachment is a bug.
3. **Budget is a hard constraint** — every action has a persistent resource envelope.
4. **Autonomy is permissioned** — research can be broad; external side effects are separately gated.
5. **Everything is auditable** — important state transitions append events.
6. **Framework-independent core** — models and agent frameworks are adapters, not the architecture.
7. **No silent self-modification** — changes to HELIS itself go through exact evaluation, git, CI and explicit approvals.
8. **Optimize expected value, not activity** — repeated reads/no-op wakes are not success.
9. **Durable state beats resident agents** — HELIS can reconstruct its control loops after a crash or reboot.
10. **Discovery and execution fail independently** — source scanning and portfolio work use separate leases.
11. **Experiment inside existing authority** — A/B testing cannot expand contact volume or bypass approval gates.
12. **Approve the destination, not just the message** — the exact public channel and endpoint are frozen before dispatch.
13. **Execute generated code behind a fixed boundary** — the model writes bounded files, never runtime commands or sandbox policy.
14. **A problem is not a product** — preserve competing economic mechanisms before deciding what system or child agents should be built.
15. **HELIS is the factory, not the child bot** — product-specific agents belong to venture-owned artifacts, not hard-coded HELIS core.

## Current boundary

HELIS now covers the constrained autonomous path from recurring market observation through **problem discovery and money-model diversification**, validation, static/manual and one sandboxed dependency-free executable MVP form, bounded B2B GTM, bounded offer/pricing and acquisition-channel experimentation, measured revenue/economics, portfolio scheduling/reallocation and controlled self-improvement.

The factory layer is now explicit: **Bot Architect → Agent Specification Language → Child Agent Factory → venture-local orchestration**. A validated money model can derive the minimum venture-specific capability graph, materialize immutable reasoning-only child agents and execute their dependency order outside HELIS core rather than turning HELIS itself into a receptionist, sales bot, support bot or other single product.

Still intentionally separate or incomplete:

- automatic tool/connector construction for child capabilities,
- automatic packaging and production deployment of a complete child venture,
- measured child-agent performance lineage and evolution,
- native SMS/social/web-form transport beyond direct Resend email and the generic contact gateway,
- general arbitrary executable-code builders, dependency installation and networked service sandboxes,
- direct payment authority,
- silent production deployment.

## Running continuously

Reference Windows Task Scheduler, Linux systemd and cron deployment assets live in `deploy/`. HELIS is intentionally host-woken rather than an unbounded resident `while True` process. One host schedule wakes market discovery and a separate schedule wakes portfolio execution. `helis-live doctor` checks the native reference schedule on both Windows and Linux without modifying it.

The recurring portfolio scheduler selects the same configured live adapters as the manual
autopilot: reviewed Vercel preview publication, Brave public prospect research, approved Resend
contact/reply observation and approved Stripe checkout/payment observation. Configuration never
replaces the persisted approval required for publication, first contact or checkout creation.
`helis-live activation-check` validates the complete configured path without calling third-party
gateways; on Windows, `Start-HelisLive.ps1` uses that gate before enabling either scheduled task.

See:

- `docs/OPERATIONS.md` — Windows Task Scheduler/systemd/cron installation, health checks, restart behavior and logs,
- `docs/SELF_IMPROVEMENT.md` — controlled self-improvement trust chain,
- `docs/ROADMAP.md` — capability roadmap,
- `docs/VALIDATION.md` — validation execution model.
