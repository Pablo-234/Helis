# AGENTS.md — HELIS engineering rules

These rules apply to humans and coding agents modifying HELIS.

## Non-negotiable invariants

1. **No external side effect bypasses policy.** Spending, outreach, publishing, credential access, deployment and self-modification must be represented as an action request and checked by the policy layer.
2. **The core is provider-independent.** Domain models, scoring, persistence and lifecycle logic must not import a vendor-specific LLM SDK.
3. **State changes are auditable.** Important lifecycle changes must append an event; do not silently mutate venture state.
4. **Evidence and inference are separate.** Never store an LLM guess as if it were observed market evidence.
5. **Cheap validation before building.** A build stage must have an explicit experiment rationale and budget.
6. **Self-modification goes through git + tests.** HELIS may propose patches; it may not silently rewrite its own running code.
7. **Sandbox untrusted execution.** Generated code and fetched content are untrusted inputs.
8. **Fail closed on uncertainty.** If policy classification is ambiguous, require approval.

## Architecture boundary

`domain -> scoring/policy -> store -> engine -> adapters`

Adapters may depend on external services. The domain must not depend on adapters.

## Definition of done

A behavioral change needs tests. A new side-effecting capability needs a policy test. A new lifecycle transition needs an audit event.
