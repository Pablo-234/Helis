# HELIS architecture

## Why a small core

HELIS is expected to live longer than today's preferred agent framework. The core therefore owns only durable concepts: evidence, opportunities, scores, budgets, policy, lifecycle state, experiments and audit events.

External model/tool stacks are adapters.

```text
Sources / Browsers / APIs
          │
          ▼
      Observations
          │
          ▼
   Opportunity discovery
          │
          ▼
 Evidence normalization
          │
          ▼
  Venture evaluation ──────► kill / explore
          │
          ▼
  Experiment planning
          │
          ▼
      POLICY GATE
          │
          ▼
 validate → build → launch → measure
          │
          └────────► scale / pivot / pause / kill
```

## Layers

### 1. Domain
Stable Pydantic models. No vendor SDKs.

### 2. Scoring
Transparent deterministic scoring first. Later an LLM may propose dimension values, but the final arithmetic remains inspectable.

### 3. Policy
Every side effect is classified before execution. Research and sandbox work can be autonomous; external contact, publication, credentials, spending and self-modification are separately gated.

### 4. Store
SQLite for v0. Important decisions are append-only audit events even when snapshots are updated for convenience.

### 5. Engine
Lifecycle orchestration. The engine does not know whether an observation came from a browser, API, human or model.

### 6. Adapters (next milestone)
- market/source scanners
- LLM reasoning providers
- browser/research tools
- builder/code agents
- deployment providers
- outreach/sales channels
- analytics and payment signals

## Planned control hierarchy

```text
HELIS Governor
├── Opportunity Scout
├── Market Researcher
├── Skeptic / Red Team
├── Experiment Designer
├── Builder
├── Go-To-Market Agent
├── Analyst
└── Capital Allocator
```

These are roles, not necessarily eight continuously-running model processes. HELIS should instantiate capability only when expected information gain justifies cost.

## Key invariant

The model can propose an action. **Policy authorizes the side effect.** This boundary must remain outside the model prompt.
