# Research notes — 2026-08-27

This document captures architectural lessons, not a commitment to any single framework.

## Findings

### Persistent agents are now practical — and expensive

Laude Institute's Headlong (released August 2026) demonstrates a continuously-thinking agent with append-only JSONL trajectories, tiered context, subagents and sandboxing. Its own documentation strongly recommends spend-capped keys and Docker; the project reports roughly $1–$2/hour at the settings used by its authors.

Useful ideas for HELIS:
- append-only trajectory/event history
- persistent goals rather than chat-session thinking
- backoff when idle
- sandbox-first generated execution
- self-improvement by branch/test/merge

We should **not** copy the always-thinking loop literally. HELIS should wake based on information value, schedules and experiments, otherwise token/electricity cost becomes the product.

Sources:
- https://www.laude.org/updates/headlong-a-microharness-for-persistent-agents
- https://github.com/laude-institute/headlong

### State persistence and explicit termination are mature patterns

Modern AutoGen AgentChat exposes save/load state for agents and teams, termination conditions, graph flows and human-in-the-loop handoffs. The important architectural lesson is explicit resumable state rather than keeping one giant conversation alive.

Sources:
- https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.state.html
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/human-in-the-loop.html

### Cost/governance is a first-class design problem

Current 2026 reporting and agent-tooling research repeatedly identifies runaway operating cost, insufficient oversight and tool security as major blockers. HELIS therefore starts with budgets and policy before it gets autonomous sales/deployment permissions.

## Decision

HELIS v0 will **not** be built on top of a monolithic agent framework.

We will build:
1. provider-independent domain models,
2. deterministic scoring,
3. append-only audit events,
4. explicit action policy,
5. resumable state,
6. narrow adapters for whichever model/browser/build tools prove best.

This lets us adopt Headlong, AutoGen, LangGraph, PydanticAI, OpenHands or future systems selectively without replacing the business brain.
