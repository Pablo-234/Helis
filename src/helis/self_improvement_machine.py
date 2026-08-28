from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from uuid import UUID

from helis.budget import CycleBudget
from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.self_improvement_domain import (
    ImprovementStatus,
    SelfImprovementCandidate,
    SelfImprovementEvaluation,
    SelfImprovementProposal,
)
from helis.self_improvement_evaluator import (
    SelfImprovementEvaluationError,
    SelfImprovementEvaluator,
)
from helis.self_improvement_gateway import SelfImprovementEvaluationGateway
from helis.self_improvement_generator import SelfImprovementGenerator
from helis.self_improvement_planner import NoImprovementSignal, SelfImprovementPlanner
from helis.self_improvement_store import SelfImprovementStore


@dataclass(slots=True)
class SelfImprovementTickReport:
    proposal_id: UUID | None = None
    candidate_id: UUID | None = None
    evaluation_id: UUID | None = None
    status: ImprovementStatus | None = None
    did_work: bool = False
    reason: str = "no_self_improvement_work"


class SelfImprovementMachine:
    """One bounded transition per tick. There is deliberately no merge operation."""

    _ACTIVE_STATUSES: ClassVar[frozenset[ImprovementStatus]] = frozenset(
        {
            ImprovementStatus.PROPOSED,
            ImprovementStatus.MATERIALIZED,
            ImprovementStatus.WAITING_EVALUATION,
            ImprovementStatus.WAITING_MERGE_APPROVAL,
        }
    )

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        *,
        repo_root: str | Path = ".",
        sandbox_root: str | Path = ".helis/self-improvement",
        evaluation_gateway: SelfImprovementEvaluationGateway | None = None,
    ) -> None:
        self.engine = engine
        self.budget = budget
        self.state = SelfImprovementStore(engine.store)
        self.planner = SelfImprovementPlanner(
            engine,
            provider,
            budget,
            repo_root=repo_root,
        )
        self.generator = SelfImprovementGenerator(
            provider,
            budget,
            repo_root=repo_root,
            sandbox_root=sandbox_root,
        )
        self.evaluator = SelfImprovementEvaluator(
            evaluation_gateway,
            sandbox_root=str(sandbox_root),
        )

    def tick(self) -> SelfImprovementTickReport:
        proposal = self._active_proposal()
        if proposal is None:
            try:
                proposal = self.propose()
            except NoImprovementSignal as exc:
                return SelfImprovementTickReport(reason=str(exc))
            return SelfImprovementTickReport(
                proposal_id=proposal.id,
                status=proposal.status,
                did_work=True,
                reason="proposal_created",
            )

        candidate = self.state.get_candidate_for_proposal(proposal.id)
        if proposal.status == ImprovementStatus.PROPOSED:
            candidate = self.materialize(proposal.id)
            return SelfImprovementTickReport(
                proposal_id=proposal.id,
                candidate_id=candidate.id,
                status=ImprovementStatus.MATERIALIZED,
                did_work=True,
                reason="candidate_materialized",
            )

        if proposal.status in {
            ImprovementStatus.MATERIALIZED,
            ImprovementStatus.WAITING_EVALUATION,
        }:
            if candidate is None:
                raise RuntimeError("self-improvement proposal lost its materialized candidate")
            if self.evaluator.gateway is None:
                return SelfImprovementTickReport(
                    proposal_id=proposal.id,
                    candidate_id=candidate.id,
                    status=proposal.status,
                    reason="evaluation_gateway_missing",
                )
            evaluation = self.evaluate(proposal.id)
            refreshed = self._require_proposal(proposal.id)
            return SelfImprovementTickReport(
                proposal_id=proposal.id,
                candidate_id=candidate.id,
                evaluation_id=evaluation.id,
                status=refreshed.status,
                did_work=True,
                reason=("candidate_accepted" if evaluation.accepted else "candidate_rejected"),
            )

        return SelfImprovementTickReport(
            proposal_id=proposal.id,
            candidate_id=candidate.id if candidate else None,
            status=proposal.status,
            reason="waiting_for_merge_approval",
        )

    def propose(self, objective: str | None = None) -> SelfImprovementProposal:
        existing = self._active_proposal()
        if existing is not None:
            return existing
        proposal = self.planner.plan(objective)
        self.state.save_proposal(proposal)
        self._event(
            "self_improvement.proposed",
            proposal.id,
            {
                "objective": proposal.objective,
                "target_files": proposal.target_files,
                "metric_name": proposal.metric_name,
                "minimum_improvement": proposal.minimum_improvement,
                "signal_ids": [str(item) for item in proposal.signal_ids],
            },
        )
        return proposal

    def materialize(self, proposal_id: UUID) -> SelfImprovementCandidate:
        proposal = self._require_proposal(proposal_id)
        existing = self.state.get_candidate_for_proposal(proposal_id)
        if existing is not None:
            return existing
        if proposal.status != ImprovementStatus.PROPOSED:
            raise RuntimeError(f"cannot materialize proposal from {proposal.status.value}")
        candidate = self.generator.materialize(proposal)
        self.state.save_candidate(candidate)
        updated = proposal.model_copy(
            update={
                "status": ImprovementStatus.MATERIALIZED,
                "updated_at": utc_now(),
            }
        )
        self.state.save_proposal(updated)
        self._event(
            "self_improvement.materialized",
            proposal.id,
            {
                "candidate_id": str(candidate.id),
                "candidate_hash": candidate.candidate_hash,
                "workspace": candidate.workspace,
                "files": [item.path for item in candidate.files],
            },
        )
        return candidate

    def evaluate(self, proposal_id: UUID) -> SelfImprovementEvaluation:
        proposal = self._require_proposal(proposal_id)
        existing = self.state.get_evaluation_for_proposal(proposal_id)
        if existing is not None:
            return existing
        if proposal.status not in {
            ImprovementStatus.MATERIALIZED,
            ImprovementStatus.WAITING_EVALUATION,
        }:
            raise RuntimeError(f"cannot evaluate proposal from {proposal.status.value}")
        candidate = self.state.get_candidate_for_proposal(proposal_id)
        if candidate is None:
            raise RuntimeError("self-improvement candidate is missing")

        waiting = proposal.model_copy(
            update={
                "status": ImprovementStatus.WAITING_EVALUATION,
                "updated_at": utc_now(),
            }
        )
        self.state.save_proposal(waiting)
        self._event(
            "self_improvement.evaluation_started",
            proposal.id,
            {"candidate_id": str(candidate.id), "candidate_hash": candidate.candidate_hash},
        )
        try:
            evaluation = self.evaluator.evaluate(waiting, candidate)
        except SelfImprovementEvaluationError as exc:
            self._event(
                "self_improvement.evaluation_failed",
                proposal.id,
                {"candidate_id": str(candidate.id), "error": str(exc)},
            )
            raise

        self.state.save_evaluation(evaluation)
        final_status = (
            ImprovementStatus.WAITING_MERGE_APPROVAL
            if evaluation.accepted
            else ImprovementStatus.REJECTED
        )
        final = waiting.model_copy(
            update={"status": final_status, "updated_at": utc_now()}
        )
        self.state.save_proposal(final)
        self._event(
            "self_improvement.evaluated",
            proposal.id,
            {
                "evaluation_id": str(evaluation.id),
                "accepted": evaluation.accepted,
                "reason": evaluation.reason,
                "baseline_metric": evaluation.baseline.metric_value,
                "candidate_metric": evaluation.candidate.metric_value,
                "regressions": evaluation.regressions,
                "final_status": final_status.value,
                "merge_available": False,
            },
        )
        return evaluation

    def _active_proposal(self) -> SelfImprovementProposal | None:
        return next(
            (
                item
                for item in self.state.list_proposals()
                if item.status in self._ACTIVE_STATUSES
            ),
            None,
        )

    def _require_proposal(self, proposal_id: UUID) -> SelfImprovementProposal:
        proposal = self.state.get_proposal(proposal_id)
        if proposal is None:
            raise RuntimeError(f"self-improvement proposal not found: {proposal_id}")
        return proposal

    def _event(self, event_type: str, entity_id: UUID, data: dict) -> None:
        self.engine.store.append_event(
            AuditEvent(event_type=event_type, entity_id=entity_id, data=data)
        )
