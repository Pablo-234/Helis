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
- [x] cron-safe scheduler wake policy + expiring single-worker lease
- [x] scheduled source scanning orchestration with an independent discovery lease
- [x] processed-observation cursor / no-work zero-call cycles
- [x] duplicate/opportunity clustering baseline
- [x] skeptic pass / falsifiable assumptions
- [x] experiment designer
- [x] policy-aware experiment ranking

Exit criterion: met. Market discovery and portfolio execution can both be host-woken safely by cron/systemd, each with an independent crash-safe lease and bounded work budget.

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
- [x] automatic two-phase cash reservation around paid external validation
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
- [x] scheduler-driven bounded GTM preparation/approved dispatch
- [x] GTM lifecycle remains active while measuring/scaling
- [x] adaptive backoff for repeated no-op acquisition wakes
- [x] bounded automatic offer A/B experiments inside the existing contact cap
- [x] automatic pricing experiments with explicit bounded price arms
- [x] deterministic arm assignment, sample caps and winner calculation from real outcomes
- [ ] automatic multi-channel acquisition experiments
- [x] response/result ingestion
- [x] revenue attribution
- [x] deterministic continue / pause / kill / scale rules over measured GTM outcomes
- [x] crash-safe response → GTM experiment/decision refresh before portfolio allocation

Exit criterion: core path met. A venture can progress from reviewed preview to bounded first contact, measured response and attributed first revenue, then test a small offer/pricing variation without increasing contact volume and receive evidence-based GTM/portfolio decisions. Automatic experimentation across different acquisition channels remains a later enhancement.

## Phase 4 — Portfolio / capital allocator

- [x] expected-value portfolio model with explicit uncertainty
- [x] currency-separated realized revenue/cost/net economics
- [x] realized ROI / cost feedback into future portfolio weights
- [x] bounded compute and cash allocation plan per venture
- [x] reserve floor and per-venture concentration cap
- [x] killed/paused ventures receive zero new allocation
- [x] scaling and measured traction influence deterministic priority
- [x] snapshot-hashed/idempotent portfolio plans
- [x] persistent cash/model-call resource envelopes
- [x] two-phase cash commitment accounting (`reserve → settle/release`)
- [x] envelope-backed venture runtime for validation/build/GTM work
- [x] priority-based bounded portfolio scheduler
- [x] scheduler skip gates for approvals/results/open commitments/exhausted capacity
- [x] crash-safe wake policy with throttling and expiring singleton lease
- [x] automatic portfolio replan + envelope rollover when GTM/economics materially change
- [x] remaining-treasury rollover never restores consumed cash/model calls
- [x] open commitments block unsafe supersession
- [x] activation-race recovery preserves the prior authoritative plan
- [x] reference systemd/cron host wake deployment

Exit criterion: met for the bounded constrained venture path. HELIS can rank competing ventures, assign scarce money/compute, enforce allocations during execution, select the next eligible funded venture, ingest market outcomes and automatically reallocate only the remaining treasury.

## Phase 5 — Controlled self-improvement

- [x] HELIS can propose bounded low-risk patches to itself
- [x] every candidate is materialized only in an isolated hash-locked sandbox
- [x] immutable eval suite compares exact baseline vs candidate behavior
- [x] baseline source hashes + candidate hash must be attested by evaluator
- [x] candidate cannot add imports, dependencies, tests or touch authority/guardrail files
- [x] no-signal cycles make zero model calls
- [x] evaluation requires measurable higher-is-better improvement with no reported regressions
- [x] explicit SELF_MODIFY run approval before reviewed branch materialization
- [x] branch gateway is bound to exact base commit, candidate hash and baseline file hashes
- [x] green review-branch CI must attest exact candidate files, branch head, Ruff and pytest
- [x] final merge requires a second explicit SELF_MODIFY approval
- [x] fresh pre-merge CI must match the approved stable attestation
- [x] merge is blocked if the review branch changes or the default branch advanced from the approved base

Exit criterion: core path met. HELIS may propose and evaluate a bounded low-authority patch, materialize the exact accepted bytes to an explicitly approved review branch, require green CI, require a second merge approval, re-attest the exact branch immediately before merge, and merge only while the default branch still equals the approved base revision. No silent live self-rewrite, rebase, force-push or approval reuse is permitted.