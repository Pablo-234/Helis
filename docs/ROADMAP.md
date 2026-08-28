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

## Phase 2 — Builder

- [x] isolated per-venture/per-run workspaces
- [x] build manifest / bounded product brief
- [x] constrained planner + generator interfaces
- [x] generated-artifact sandbox with path containment
- [x] deterministic test-before-preview requirement
- [x] preview manifest with content hash
- [x] policy-gated preview publication transport
- [x] hash-lock: only the exact reviewed artifact can be published
- [x] run-scoped publication approval
- [x] HTTPS-only operator-configured preview gateway
- [x] reusable `static_web_v1` and `concierge_ops_v1` templates
- [x] adversarial model review after deterministic checks
- [x] shared model budget + per-build file/byte stop conditions
- [x] explicit promotion from `validated` → `building` → `ready_preview`
- [x] bounded automatic repair loop with a default two-attempt cap
- [ ] executable-code sandbox backend with network/resource isolation

Exit criterion: met for constrained MVP artifacts. A validated venture can become a verified/reviewed artifact, repair one failed build, and be published through an explicit policy-gated transport without rebuilding or mutating the reviewed bytes. General executable software builders remain a future capability rather than a requirement for entering go-to-market.

## Phase 3 — Go-to-market

- [x] prospect discovery with evidence-bound lead reasons
- [x] per-venture CRM/event trail
- [x] outreach drafts and run-scoped approval
- [x] bounded contact batches / anti-spam limits
- [ ] automatic multi-channel acquisition experiments
- [ ] automatic pricing experiments
- [x] response/result ingestion
- [x] revenue attribution
- [x] deterministic continue / pause / kill / scale rules over measured GTM outcomes

Exit criterion: core path met. A venture can progress from reviewed preview to bounded first contact, measured response and attributed first revenue, then receive an evidence-based GTM allocation decision. Automatic channel/pricing experimentation remains a later enhancement.

## Phase 4 — Portfolio / capital allocator

- [ ] expected-value portfolio model with explicit uncertainty
- [x] bounded compute and cash allocation plan per venture
- [x] reserve floor and per-venture concentration cap
- [x] killed/paused ventures receive zero new allocation
- [x] scaling and measured traction influence deterministic priority
- [x] snapshot-hashed/idempotent portfolio plans
- [ ] automatic execution of approved resource envelopes
- [ ] realized ROI / cost feedback into future portfolio weights

Exit criterion: HELIS allocates scarce money/compute between competing ventures instead of merely running them all. Initial allocator plans resources only; actual spending/execution remains separately gated.

## Phase 5 — Controlled self-improvement

- [ ] HELIS can propose patches to itself
- [ ] every patch runs in a branch/sandbox
- [ ] eval suite compares old vs new behavior
- [ ] merge only after measurable improvement and policy approval

No silent live self-rewrite.
