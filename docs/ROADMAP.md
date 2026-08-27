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

Exit criterion: met for an operator-triggered cycle. Scheduled waking remains an infrastructure enhancement rather than a prerequisite for Phase 1.

## Phase 1 — Validation machine (current)

- [x] experiment execution state machine
- [x] desk-research validation adapter over real observation corpus
- [ ] interview adapter
- [ ] pricing adapter
- [ ] smoke-test adapter
- [ ] concierge adapter
- [x] validation execution/cash budget
- [x] automatic result ingestion for built-in adapters
- [x] generic external result ingestion path
- [x] run-scoped approval state
- [x] explicit advance / continue / pivot / kill decisions
- [x] deterministic decision thresholds outside the model
- [x] automatic follow-up experiment generation after inconclusive/insufficient evidence

Exit criterion: HELIS can already reject a strongly falsified idea using real-world collected evidence and autonomously plan the next information-gaining test when evidence is insufficient. Full Phase 1 exit additionally requires at least one live customer-facing validation adapter.

## Phase 2 — Builder

- [ ] isolated code workspaces
- [ ] test-before-deploy requirement
- [ ] preview deployments
- [ ] reusable venture templates
- [ ] self-review + adversarial review

Exit criterion: validated ideas can become constrained MVPs without editing HELIS core.

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
