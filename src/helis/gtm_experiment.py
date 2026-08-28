from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from helis.budget import CycleBudget
from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.gtm_domain import Lead, LeadResponseKind
from helis.gtm_experiment_domain import (
    GTMArmMetrics,
    GTMExperiment,
    GTMExperimentArm,
    GTMExperimentSnapshot,
    GTMExperimentStatus,
)
from helis.gtm_experiment_planner import GTMExperimentPlanner
from helis.gtm_experiment_store import GTMExperimentStore
from helis.gtm_lifecycle import gtm_is_active
from helis.gtm_store import GTMStore, lead_identity
from helis.model_provider import ModelProvider


@dataclass(slots=True)
class GTMExperimentPlanResult:
    experiment: GTMExperiment | None
    created: bool = False


class GTMExperimentManager:
    """Plans one bounded offer experiment, balances assignments and scores real outcomes."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider | None = None,
        budget: CycleBudget | None = None,
    ) -> None:
        self.engine = engine
        self.gtm = GTMStore(engine.store)
        self.experiments = GTMExperimentStore(engine.store)
        self.planner = (
            GTMExperimentPlanner(provider, budget)
            if provider is not None and budget is not None
            else None
        )

    def plan_if_eligible(self, opportunity_id: UUID) -> GTMExperimentPlanResult:
        existing = self.experiments.latest(opportunity_id)
        if existing is not None:
            return GTMExperimentPlanResult(existing, created=False)
        responses = self.gtm.list_responses(opportunity_id)
        if not responses:
            return GTMExperimentPlanResult(None, created=False)
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None or not gtm_is_active(opportunity.stage):
            return GTMExperimentPlanResult(None, created=False)
        if self.planner is None:
            return GTMExperimentPlanResult(None, created=False)

        validation_results = self.engine.store.list_validation_results(opportunity_id)
        experiment = self.planner.plan(opportunity, validation_results, responses)
        self.experiments.save(experiment)
        self.engine.store.append_event(
            AuditEvent(
                event_type="gtm.experiment_planned",
                entity_id=experiment.id,
                data={
                    "opportunity_id": str(opportunity_id),
                    "kind": experiment.kind.value,
                    "hypothesis": experiment.hypothesis,
                    "arms": [
                        {
                            "key": arm.key,
                            "label": arm.label,
                            "price_cents": arm.price_cents,
                            "currency": arm.currency.upper(),
                        }
                        for arm in experiment.arms
                    ],
                    "max_assignments_per_arm": experiment.max_assignments_per_arm,
                },
            )
        )
        return GTMExperimentPlanResult(experiment, created=True)

    def assign_for_leads(
        self,
        opportunity_id: UUID,
        leads: list[Lead],
    ) -> dict[UUID, GTMExperimentArm]:
        if not leads:
            return {}
        experiment = self.experiments.latest(opportunity_id)
        if experiment is None:
            return {}
        by_key = {arm.key: arm for arm in experiment.arms}

        if experiment.status == GTMExperimentStatus.COMPLETED:
            if experiment.winner_arm_key is None:
                return {}
            winner = by_key[experiment.winner_arm_key]
            return {lead.id: winner for lead in leads}

        counts = {key: 0 for key in by_key}
        for draft in self.gtm.list_drafts(opportunity_id):
            if draft.experiment_id != experiment.id or draft.experiment_arm_key not in counts:
                continue
            counts[draft.experiment_arm_key] += 1

        assignments: dict[UUID, GTMExperimentArm] = {}
        for lead in sorted(leads, key=lead_identity):
            available = [
                key
                for key in sorted(by_key)
                if counts[key] < experiment.max_assignments_per_arm
            ]
            if not available:
                break
            smallest = min(counts[key] for key in available)
            tied = [key for key in available if counts[key] == smallest]
            digest = hashlib.sha256(lead_identity(lead).encode("utf-8")).digest()
            chosen = tied[int.from_bytes(digest[:4], "big") % len(tied)]
            assignments[lead.id] = by_key[chosen]
            counts[chosen] += 1
        return assignments

    def experiment_for_drafting(self, opportunity_id: UUID) -> GTMExperiment | None:
        return self.experiments.latest(opportunity_id)

    def refresh(self, opportunity_id: UUID) -> GTMExperimentSnapshot | None:
        experiment = self.experiments.active(opportunity_id)
        if experiment is None:
            return None
        by_key = {arm.key: GTMArmMetrics(arm_key=arm.key) for arm in experiment.arms}
        scores = {arm.key: 0.0 for arm in experiment.arms}

        for draft in self.gtm.list_drafts(opportunity_id):
            if draft.experiment_id != experiment.id or draft.experiment_arm_key not in by_key:
                continue
            by_key[draft.experiment_arm_key].assigned += 1

        for response in self.gtm.list_responses(opportunity_id):
            run = self.gtm.get_outreach_run(response.run_id)
            if run is None:
                continue
            draft = self.gtm.get_draft(run.draft_id)
            if (
                draft is None
                or draft.experiment_id != experiment.id
                or draft.experiment_arm_key not in by_key
            ):
                continue
            metrics = by_key[draft.experiment_arm_key]
            metrics.resolved += 1
            metrics.revenue_cents += response.revenue_cents
            score = self._outcome_score(response.kind)
            scores[draft.experiment_arm_key] += score
            if response.kind == LeadResponseKind.SALE:
                metrics.sales += 1
            elif response.kind == LeadResponseKind.MEETING:
                metrics.meetings += 1
            elif response.kind == LeadResponseKind.INTERESTED:
                metrics.interested += 1

        for key, metrics in by_key.items():
            if metrics.resolved:
                metrics.outcome_score = scores[key] / metrics.resolved

        control = by_key["control"]
        variant = by_key["variant"]
        completed = False
        winner: str | None = None
        conclusion = (
            f"collecting evidence: control={control.resolved}, variant={variant.resolved} resolved"
        )
        enough = min(control.resolved, variant.resolved) >= experiment.minimum_resolved_per_arm
        if enough:
            lift = variant.outcome_score - control.outcome_score
            if abs(lift) >= experiment.minimum_lift:
                completed = True
                winner = "variant" if lift > 0 else "control"
                conclusion = (
                    f"{winner} won on deterministic outcome score; absolute lift={abs(lift):.3f}"
                )
            elif min(control.resolved, variant.resolved) >= experiment.max_resolved_per_arm:
                completed = True
                conclusion = (
                    "experiment reached its resolved-sample cap without the minimum outcome lift"
                )

        if completed:
            experiment = experiment.model_copy(
                update={
                    "status": GTMExperimentStatus.COMPLETED,
                    "winner_arm_key": winner,
                    "conclusion": conclusion,
                    "updated_at": utc_now(),
                }
            )
            self.experiments.save(experiment)
            self.engine.store.append_event(
                AuditEvent(
                    event_type="gtm.experiment_completed",
                    entity_id=experiment.id,
                    data={
                        "opportunity_id": str(opportunity_id),
                        "winner_arm_key": winner,
                        "conclusion": conclusion,
                        "control_score": control.outcome_score,
                        "variant_score": variant.outcome_score,
                        "control_resolved": control.resolved,
                        "variant_resolved": variant.resolved,
                    },
                )
            )

        return GTMExperimentSnapshot(
            experiment_id=experiment.id,
            arms=[control, variant],
            completed=completed,
            winner_arm_key=winner,
            conclusion=conclusion,
        )

    @staticmethod
    def _outcome_score(kind: LeadResponseKind) -> float:
        if kind == LeadResponseKind.SALE:
            return 1.0
        if kind == LeadResponseKind.MEETING:
            return 0.75
        if kind == LeadResponseKind.INTERESTED:
            return 0.5
        return 0.0
