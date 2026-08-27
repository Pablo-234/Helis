# HELIS validation machine

## Purpose

Validation exists to buy information before HELIS buys product development.

The durable unit is an **ExperimentRun**, not an LLM conversation. Every run has a persisted state and every important transition emits an audit event.

```text
PLANNED
  ├─ policy gate fails ──> WAITING_APPROVAL
  └─ allowed ───────────> READY
                            ↓
                         RUNNING
                         /     \
                  COMPLETED   FAILED

unsupported adapter → BLOCKED
```

A waiting run can receive a one-time approval. That does not relax the global autonomy policy for any other action.

## Built-in autonomous executor

`desk_research_corpus_v1` tests a planned desk-research experiment against market observations already collected by HELIS.

The model receives only a bounded relevant subset of the corpus. A positive or negative result is accepted only when it cites observation IDs that actually appeared in that subset. Unknown IDs or uncited directional conclusions fail the run instead of becoming evidence.

## Final venture decision

The final transition is deterministic and lives outside model prompts.

- **KILL**: any negative result with confidence >= 0.88, or accumulated negative confidence >= 1.3.
- **ADVANCE**: positive confidence weight >= 1.4 across at least two independent experiment types, with negative weight < 0.5.
- **PIVOT**: a confidence >= 0.6 pivot signal plus weak/negative evidence for the current hypothesis.
- **CONTINUE**: everything else.

ADVANCE moves the venture to `validated`, not `building`; the builder phase must explicitly claim it later.

## Current external-action boundary

Interview, pricing, smoke-test and concierge experiments can be planned and persisted. Because they generally require customer contact, publication or spending, default policy sends them to `waiting_approval`. Live adapters will be added separately so approval cannot accidentally mean "invent a side effect implementation inside a prompt".
