# HELIS roadmap

## Phase 0 — Business brain foundations

- [x] stable venture/evidence domain
- [x] append-only audit events
- [x] deterministic score computation
- [x] autonomy/spend policy gate
- [x] bounded model-call/token/cost budget
- [x] evidence-bound opportunity scout
- [x] evidence-bound venture analyst
- [x] bounded observe → discover → evaluate cycle
- [x] RSS + GitHub issue + Hacker News source adapters
- [x] configurable source registry
- [ ] scheduled scanning / wake policy
- [x] processed-observation cursor / no-work zero-call cycles
- [x] duplicate/opportunity clustering baseline
- [x] skeptic pass / falsifiable assumptions
- [x] experiment designer
- [x] policy-aware experiment ranking

Exit criterion: met for an operator-triggered cycle. Scheduled waking remains an infrastructure enhancement.

## Phase 1 — Validation machine

- [x] experiment execution state machine
- [x] desk-research validation adapter over real observation corpus
- [x] approved HTTPS external validation gateway for interview/pricing transport
- [x] asynchronous `waiting_result` state
- [x] idempotent external dispatch
- [x] executor-level forced approval independent of model flags
- [ ] native direct interview channel adapter
- [ ] native direct pricing channel adapter
- [ ] smoke-test publication adapter
- [ ] concierge adapter
- [x] validation execution/cash budget
- [x] automatic result ingestion for built-in adapters
- [x] generic external result ingestion path
- [x] run-scoped approval state
- [x] explicit advance / continue / pivot / kill decisions
- [x] deterministic decision thresholds outside the model
- [x] automatic follow-up experiment generation after insufficient evidence

Exit criterion: met at the transport layer when an approved validation gateway is configured.

## Phase 2 — Builder (current)

- [x] isolated per-venture/per-run workspaces
- [x] build manifest / bounded product brief
- [x] constrained planner + generator interfaces
- [x] generated-artifact sandbox with path containment
- [x] deterministic test-before-preview requirement
- [x] preview manifest with content hash
- [ ] actual preview deployment adapter
- [x] reusable `static_web_v1` and `concierge_ops_v1` templates
- [x] adversarial model review after deterministic checks
- [x] shared model budget + per-build file/byte stop conditions
- [x] explicit promotion from `validated` → `building` → `ready_preview`
- [x] bounded automatic repair loop with a default two-attempt cap
- [ ] executable-code sandbox backend with network/resource isolation

Exit criterion: almost met for constrained artifacts. A validated venture can become a tested local preview and repair one failed attempt automatically. Publishing the exact reviewed hash through a policy-gated preview transport is the next slice.

## Phase 3 — Go-to-market

- [ ] prospect discovery
- [ ] outreach drafts and approval tiers
- [ ] channel experiments
- [ ] CRM/event trail
- [ ] pricing experiments
- [ ] revenue attribution

Exit criterion: a venture can progress from discovered problem to measured first revenue.

## Phase 4 — Portfolio / capital allocator

- [ ] expected-value portfolio ranking
- [ ] compute and cash budgets per venture
- [ ] stop-loss rules
- [ ] scaling thresholds
- [ ] automatic resource reallocation

Exit criterion: HELIS allocates scarce money/compute between competing ventures instead of merely running them all.

## Phase 5 — Controlled self-improvement

- [ ] HELIS can propose patches to itself
- [ ] every patch runs in a branch/sandbox
- [ ] eval suite compares old vs new behavior
- [ ] merge only after measurable improvement and policy approval

No silent live self-rewrite.
