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

## Phase 2 — Builder (current)

- [x] isolated per-venture/per-run workspaces
- [x] build manifest with per-file hashes and bundle digest
- [x] bounded BuildSpec product brief
- [x] provider-independent planner/generator boundary
- [x] generated-file allowlist + path/size limits
- [x] offline static-web verifier
- [x] Python stdlib Docker sandbox with network disabled
- [x] no host fallback if Docker/image is unavailable
- [x] fixed verifier commands; model cannot provide shell commands
- [x] test-before-preview requirement
- [x] builder model-call/token/cost budget integration
- [x] explicit promotion from `validated` → `building`
- [x] persisted build runs and audit events
- [x] `helis build` / `helis build-status`
- [ ] automated repair loop after failed verification
- [ ] self-review + adversarial build review
- [ ] reusable venture templates
- [ ] preview deployment adapter
- [ ] preview policy gate / expiry
- [ ] automatic preview smoke checks

Current milestone: a validated venture can become a constrained, locally verified MVP workspace without modifying HELIS core or silently deploying anything.

Exit criterion for full Phase 2: a tested build can be promoted to an isolated preview deployment with a bounded lifetime and no production credentials.

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
