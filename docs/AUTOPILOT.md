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
