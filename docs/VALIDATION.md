# HELIS validation machine

## Purpose

Validation exists to buy information before HELIS buys product development.

The durable unit is an **ExperimentRun**, not an LLM conversation. Every important transition is persisted and audited.

```text
PLANNED
  ├─ approval needed ──> WAITING_APPROVAL
  └─ safe/local ───────> READY
                          ↓
                       RUNNING
                    /     |      \
            COMPLETED  FAILED  WAITING_RESULT
                                ↓
                         external result
                                ↓
                           COMPLETED

unsupported adapter → BLOCKED
```

A waiting run can receive one-time approval. That approval belongs only to that run.

## Built-in autonomous executor

`desk_research_corpus_v1` tests a desk-research experiment against market observations already collected by HELIS. A positive or negative result is accepted only when it cites observation IDs actually present in the bounded corpus supplied to the model.

## Approved external validation gateway

`approved_validation_gateway_v1` is the first external side-effect bridge.

It is deliberately narrow:

- the destination comes only from `HELIS_VALIDATION_GATEWAY_URL`, never from a model response;
- remote destinations must use HTTPS;
- credentials cannot be embedded in the URL;
- query parameters/fragments are rejected from the configured destination;
- every dispatch executor declares `requires_run_approval=True` outside the prompt;
- the run ID is sent as an `Idempotency-Key` so retries/restarts do not imply duplicate customer contact;
- the experiment cost and duration caps are sent as constraints;
- a successful dispatch moves the run to `waiting_result` instead of pretending a human response exists immediately.

HELIS currently connects this gateway to `interview` and `pricing` experiments. Smoke-test publication, concierge execution and direct channel integrations remain separate capability boundaries.

The gateway acknowledges a dispatch with:

```json
{
  "accepted": true,
  "dispatch_id": "your-external-id",
  "metadata": {}
}
```

A later external result must reference the exact HELIS `run_id`, `experiment_id` and `opportunity_id`. HELIS refuses mismatched and duplicate results.

## Final venture decision

The final transition is deterministic and outside model prompts.

- **KILL**: any negative result with confidence >= 0.88, or accumulated negative confidence >= 1.3.
- **ADVANCE**: positive confidence weight >= 1.4 across at least two independent experiment types, with negative weight < 0.5.
- **PIVOT**: a confidence >= 0.6 pivot signal plus weak/negative evidence for the current hypothesis.
- **CONTINUE**: everything else; HELIS may plan one new experiment that reduces the largest remaining uncertainty.

ADVANCE moves the venture to `validated`, not `building`.

## Result-before-action ordering

Persisted external results are reconciled **before** another validation action is executed. A new strong negative result can therefore kill a venture without HELIS first launching one more experiment from stale state.

## Budget behavior

Model reasoning and validation cash are separate budgets. External dispatch reserves the experiment's declared `max_cost_cents` against the validation cash envelope. The normal default remains zero cash.

If a reported actual cost exceeds the planned maximum, the result is retained (the real-world event already happened) and HELIS appends an explicit `experiment.cost_overrun` audit event.
