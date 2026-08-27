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

Exit criterion: met at the transport layer when an approved validation gateway is configured. HELIS can execute a real external validation action without granting blanket contact permission, persist the asynchronous run, ingest the result and decide what to do next. Native channel adapters remain optional refinements.

## Phase 2 — Builder (next)

- [ ] isolated per-venture workspaces
- [ ] build manifest / bounded product brief
- [ ] builder adapter interface
- [ ] generated-code sandbox
- [ ] test-before-preview requirement
- [ ] preview deployments
- [ ] reusable venture templates
- [ ] self-review + adversarial review
- [ ] builder budget and stop conditions
- [ ] explicit promotion from `validated` → `building`

Exit criterion: a validated venture can become a constrained, tested preview MVP without modifying the HELIS core or silently deploying to production.

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
