# HELIS Autopilot

`helis-autopilot` is the top-level zero-idea operating mode for HELIS.

It does **not** ask the operator to choose a niche, product, service, customer type or bot. It starts
from configured public market sources and tries to create online ventures from evidence.

## Run

```bash
helis-autopilot run
```

Defaults:

- reads public sources from `helis.toml`,
- uses the model configured by `HELIS_LLM_BASE_URL` / `HELIS_LLM_MODEL`,
- autonomous cash treasury is `0`,
- allocates bounded model-call capacity across the strongest ventures,
- considers only money models with remotely deliverable online delivery modes,
- advances multiple lifecycle checkpoints in one invocation,
- stops at an existing HELIS approval/result/gateway boundary instead of bypassing it.

No business idea is an input to this command.

## Controlled first run

Use the stricter wrapper before a live autopilot run:

```bash
helis-live bootstrap
helis-live doctor --probe-model
helis-live pilot
```

The wrapper still executes the normal `AutonomousOnlineVentureOperator`, but fixes cash and configured model cost at zero, accepts only an uncredentialed localhost model, limits the portfolio to one venture and omits every external-write gateway. The resulting report and any operator requests are persisted. This mode is intended to prove the complete internal path before granting HELIS real-world hands.

## Lifecycle

```text
PUBLIC INTERNET SOURCES
        ↓
OBSERVATIONS
        ↓
PROBLEM DISCOVERY
        ↓
2–5 MONEY MODELS
        ↓
ONLINE-ONLY FILTER
        ↓
ANALYST + SKEPTIC
        ↓
CHEAPEST FALSIFICATION EXPERIMENT
        ↓
PORTFOLIO BOOTSTRAP
        ↓
VALIDATE
        ↓
BOT ARCHITECT
        ↓
AGENT SPECS / CHILD ARTIFACTS IF NEEDED
        ↓
BUILD
        ↓
GTM / MARKET FEEDBACK
        ↓
SCALE / PIVOT / PAUSE / KILL
```

The venture may use zero AI child agents if deterministic automation, a human step or an external
service is the better operating design. HELIS itself remains the factory/operator rather than one
hard-coded child business.

## Venture-local orchestration

Generated child agents can be executed as one persistent capability graph:

```bash
helis-agent orchestrate <OPPORTUNITY_ID> --task "<venture-local input>" --source-key "<idempotency-key>"
helis-agent orchestration-status <RUN_ID>
helis-agent supply-capability-result <RUN_ID> <CAPABILITY_KEY> --output "<observed result>"
helis-agent orchestration-resume <RUN_ID>
```

The graph is locked to one opportunity, one architecture snapshot, one spec bundle and the exact
materialized child artifacts. All child calls share one persisted model/token/cost ceiling. Only
completed dependency outputs flow forward. Human, deterministic and external-service nodes are
never fabricated by a language model: the graph stops until an observed result is supplied and
audited. The current child runtime remains `reasoning_only_v1`, so declared future tools do not
become executable authority through orchestration.

## Online-only boundary

Autopilot adds a deterministic filter after model generation. `physical_ops`, `hybrid` and generic
`other` delivery modes are not admitted to the autonomous online portfolio. Accepted ventures are
tagged `online_venture`.

The model is also instructed not to propose businesses that depend on inventory, manufacturing,
food, transport, property, on-site labor or local presence.

## Resource continuity

Autopilot may bootstrap the first portfolio when no funded plan exists. Once a funded plan exists,
it does **not** create a new full treasury on every run. Existing portfolio reallocation owns the
remaining-resource rollover so consumed cash/model calls do not reappear.

## Real-world gates

Autopilot does not turn model output into unrestricted external authority. Existing HELIS gates still
apply to actions such as external contact, publication, paid validation, spending and credentials.
Reaching one of these gates is reported as `real_world_gate` with the exact blocker.

That means HELIS can autonomously get from zero idea to the next real-world action, but an operator
must configure/approve the relevant external gateway before actions that require that authority can
actually happen.

## Status

```bash
helis-autopilot status
```

This is read-only and makes no model or network calls. It shows the latest portfolio plus all
persisted `online_venture` business models and their lifecycle stages.
