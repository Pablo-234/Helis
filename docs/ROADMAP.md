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

Exit criterion: met. Market discovery and portfolio execution can be host-woken safely by Windows Task Scheduler, cron or systemd, each with an independent crash-safe lease and bounded work budget.

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
- [x] constrained `python_service_v1` executable template behind an operator-enabled sandbox
- [x] executable-code sandbox backend with external network disabled and hard memory/CPU/PID/time limits
- [x] fixed non-shell unittest command, read-only generated workspace and non-root/cap-drop container execution
- [x] AST defense-in-depth for imports, introspection, top-level side effects and minimum test behavior

Exit criterion: met for constrained MVP artifacts including one dependency-free executable workflow core. A validated venture can become a verified/reviewed static, concierge or sandbox-tested Python artifact, repair one failed build, and reach hash-locked preview without giving the model shell/runtime authority. General arbitrary executable software, dependencies, production deployment and networked services remain outside this builder boundary.

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
- [x] automatic multi-channel acquisition experiments over comparable dual-channel leads
- [x] multiple persisted public contact options with exact selected endpoint approval hash-lock
- [x] response/result ingestion
- [x] revenue attribution
- [x] deterministic continue / pause / kill / scale rules over measured GTM outcomes
- [x] crash-safe response → GTM experiment/decision refresh before portfolio allocation

Exit criterion: met for bounded B2B go-to-market. A venture can progress from reviewed preview to approved first contact, measured response and attributed revenue, then test bounded offer/pricing and public contact-channel variations without increasing contact volume. Commercial and channel assignments, sample caps, exact selected endpoints and winners are persisted and deterministic outside the model.

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
- [x] reference Windows Task Scheduler/systemd/cron host wake deployment
- [x] recurring scheduler routes the complete configured preview/prospect/contact/result/commerce adapter set

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

## Phase 6 — Autonomous venture factory

- [x] one evidence-backed problem can produce multiple structurally different money-making models in the same bounded discovery call
- [x] explicit payer / offer / revenue / delivery / pricing / acquisition / fulfillment hypotheses
- [x] explicit automation-vs-human operating roles and target owner effort
- [x] deterministic pre-validation economic-shape heuristic; model does not award its own score
- [x] business-model-aware dedup keeps distinct monetization strategies separate while reinforcing true repeats
- [x] downstream analyst/skeptic treat generated economics as hypotheses rather than evidence
- [x] Bot Architect: snapshot-bound minimal capability DAG over deterministic automation / AI agent / human / external service
- [x] architecture policy caps graph/AI-agent size, forbids child SELF_MODIFY and requires venture isolation
- [x] fresh validated architecture is an explicit autonomous checkpoint before the builder; unchanged snapshots cost zero additional model calls
- [x] Agent Specification Language: one typed child-agent contract per AI capability, never for deterministic/human/external capabilities
- [x] agent specs inherit goal/IO/success metric from architecture and cannot broaden capability authority
- [x] symbolic tool/credential requirements, bounded memory/turn/tool scopes and venture/customer-data isolation policy
- [x] stale architecture blocks spec generation before a model call; zero-AI architecture produces an empty bundle with zero calls
- [x] fresh non-empty agent specs are a second autonomous checkpoint before the builder
- [x] Child Agent Factory: build isolated venture-owned agents from approved specs rather than hard-coding product bots into HELIS
- [x] persistent venture-level capability-DAG orchestration with strict venture/artifact/spec/resource isolation
- [x] explicit audited result gates for human, deterministic and external-service dependencies
- [x] graph-wide model/token/cost budget that survives orchestration resume
- [x] unified hash-confirmed operator inbox for all pending approvals and non-AI result gates
- [x] bootstrap/readiness doctor and durable localhost-only zero-cash controlled pilot
- [x] exact local-model inventory diagnosis and one-completion bounded JSON smoke test
- [ ] bounded tool/connector factory for missing capabilities
- [ ] autonomous venture packaging/deployment workflow behind explicit side-effect gates
- [ ] child-agent performance lineage and bounded evolution from measured economic outcomes
- [ ] portfolio allocator uses validated business-model economics and child-agent operating cost to compound capital

Exit criterion: HELIS is a factory rather than a single product. It can discover a problem, preserve several competing economic mechanisms, validate one, derive the minimum system of capabilities/agents needed to operate it, build those child artifacts outside HELIS core, and measure the resulting business independently.
