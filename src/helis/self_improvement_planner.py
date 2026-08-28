from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import AuditEvent
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.self_improvement_domain import ImprovementSignal, SelfImprovementProposal
from helis.self_improvement_policy import SelfImprovementPolicy


class NoImprovementSignal(RuntimeError):
    pass


class ImprovementPlanPayload(BaseModel):
    objective: str = Field(min_length=10, max_length=1200)
    rationale: list[str] = Field(min_length=1, max_length=8)
    target_files: list[str] = Field(min_length=1, max_length=2)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)
    metric_name: str = Field(min_length=2, max_length=120)
    minimum_improvement: float = Field(gt=0, le=1000)


SYSTEM_PROMPT = """You are HELIS Self-Improvement Planner.
Propose one SMALL, LOW-RISK improvement to HELIS itself from the supplied objective and/or audit signals.
You are planning only; do not write code. Choose at most two target files and ONLY from allowed_targets.
Do not request changes to tests, policy, gateways, credentials, cash/accounting, deployment, CI,
resource envelopes, or self-improvement guardrails. The acceptance criteria must be measurable.
Choose a metric that an isolated evaluator can compare numerically between baseline and candidate.
Avoid broad refactors. Prefer a narrow bug fix, deterministic quality improvement, or efficiency gain.
Return JSON only with: objective, rationale, target_files, acceptance_criteria, metric_name,
minimum_improvement.
"""


class ImprovementSignalCollector:
    """Extracts repeated operational pain from the append-only audit log without a model call."""

    def __init__(self, engine: HelisEngine) -> None:
        self.engine = engine

    def collect(self, *, scan_limit: int = 100, max_signals: int = 8) -> list[ImprovementSignal]:
        with self.engine.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM events ORDER BY seq DESC LIMIT ?",
                (max(1, min(scan_limit, 500)),),
            ).fetchall()
        output: list[ImprovementSignal] = []
        for row in rows:
            event = AuditEvent.model_validate_json(row["payload"])
            summary = self._interesting_summary(event)
            if summary is None:
                continue
            output.append(
                ImprovementSignal(
                    event_id=event.id,
                    event_type=event.event_type,
                    summary=summary,
                    created_at=event.created_at,
                )
            )
            if len(output) >= max_signals:
                break
        return output

    @staticmethod
    def _interesting_summary(event: AuditEvent) -> str | None:
        event_type = event.event_type.lower()
        if "failed" in event_type or "error" in event_type:
            return f"{event.event_type}: {json.dumps(event.data, ensure_ascii=False)[:800]}"
        if event.event_type == "portfolio.scheduler_backoff":
            streak = int(event.data.get("consecutive_noops", 0) or 0)
            if streak >= 3:
                reason = str(event.data.get("reason", "unknown"))
                return f"repeated scheduler no-op ({streak}x): {reason}"
        if event.event_type == "portfolio.scheduler_tick":
            failed = int(event.data.get("failed", 0) or 0)
            if failed > 0:
                return f"scheduler tick contained {failed} failed venture runtime attempt(s)"
        return None


class SelfImprovementPlanner:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        *,
        repo_root: str | Path = ".",
        policy: SelfImprovementPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.budget = budget
        self.repo_root = Path(repo_root).resolve()
        self.policy = policy or SelfImprovementPolicy()
        self.signals = ImprovementSignalCollector(engine)

    def plan(self, objective: str | None = None) -> SelfImprovementProposal:
        signals = self.signals.collect()
        explicit = (objective or "").strip()
        if not explicit and not signals:
            raise NoImprovementSignal("no recent failure/backoff signal justifies self-improvement")
        allowed_targets = self.policy.catalog(self.repo_root)
        if not allowed_targets:
            raise NoImprovementSignal("no allowlisted source files are present in the checkout")

        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "explicit_objective": explicit or None,
                    "audit_signals": [item.model_dump(mode="json") for item in signals],
                    "allowed_targets": allowed_targets,
                    "constraints": {
                        "max_files": self.policy.MAX_FILES,
                        "tests_are_immutable": True,
                        "live_checkout_writes_forbidden": True,
                        "merge_not_available": True,
                    },
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = ImprovementPlanPayload.model_validate_json(result.content)
        proposal = SelfImprovementProposal(
            objective=payload.objective,
            rationale=payload.rationale,
            signal_ids=[item.event_id for item in signals],
            target_files=payload.target_files,
            acceptance_criteria=payload.acceptance_criteria,
            metric_name=payload.metric_name,
            minimum_improvement=payload.minimum_improvement,
        )
        self.policy.validate_proposal(proposal, self.repo_root)
        return proposal
