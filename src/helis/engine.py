from __future__ import annotations

from dataclasses import dataclass

from helis.dedup import find_duplicate, merge_opportunities
from helis.domain import (
    AuditEvent,
    Experiment,
    Observation,
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    SkepticReport,
    VentureStage,
)
from helis.scoring import score_opportunity
from helis.store import HelisStore


@dataclass(slots=True)
class RankedOpportunity:
    opportunity: Opportunity
    scorecard: Scorecard


class HelisEngine:
    def __init__(self, store: HelisStore, *, duplicate_threshold: float = 0.72) -> None:
        self.store = store
        self.duplicate_threshold = duplicate_threshold
        self.store.initialize()

    def observe(self, observation: Observation) -> Observation:
        inserted = self.store.save_observation(observation)
        if inserted:
            self.store.append_event(
                AuditEvent(
                    event_type="market.observed",
                    entity_id=observation.id,
                    data={"source": observation.source},
                )
            )
        return observation

    def ingest(self, opportunity: Opportunity) -> Opportunity:
        duplicate = find_duplicate(
            opportunity,
            self.store.list_opportunities(),
            threshold=self.duplicate_threshold,
        )
        if duplicate is not None:
            existing, similarity = duplicate
            merged = merge_opportunities(existing, opportunity)
            self.store.save_opportunity(merged)
            self.store.append_event(
                AuditEvent(
                    event_type="opportunity.reinforced",
                    entity_id=existing.id,
                    data={
                        "candidate_id": str(opportunity.id),
                        "similarity": similarity,
                        "evidence_count": len(merged.evidence),
                    },
                )
            )
            return merged

        self.store.save_opportunity(opportunity)
        self.store.append_event(
            AuditEvent(
                event_type="opportunity.discovered",
                entity_id=opportunity.id,
                data={"title": opportunity.title, "evidence_count": len(opportunity.evidence)},
            )
        )
        return opportunity

    def evaluate(self, opportunity: Opportunity, dimensions: ScoreDimensions) -> Scorecard:
        scorecard = score_opportunity(opportunity, dimensions)
        self.store.save_scorecard(scorecard)

        stage = (
            VentureStage.KILLED
            if scorecard.recommendation == Recommendation.KILL
            else VentureStage.EVALUATED
        )
        evaluated = opportunity.model_copy(update={"stage": stage})
        self.store.save_opportunity(evaluated)
        self.store.append_event(
            AuditEvent(
                event_type="opportunity.evaluated",
                entity_id=opportunity.id,
                data={
                    "score": scorecard.total,
                    "recommendation": scorecard.recommendation.value,
                    "stage": stage.value,
                },
            )
        )
        return scorecard

    def record_skeptic_report(self, report: SkepticReport) -> None:
        self.store.save_skeptic_report(report)
        self.store.append_event(
            AuditEvent(
                event_type="opportunity.challenged",
                entity_id=report.opportunity_id,
                data={
                    "assumption_count": len(report.assumptions),
                    "max_assumption_risk": report.max_assumption_risk,
                },
            )
        )

    def plan_experiment(self, experiment: Experiment, *, executable: bool) -> None:
        self.store.save_experiment(experiment)
        opportunity = self.store.get_opportunity(experiment.opportunity_id)
        if opportunity is not None:
            validating = opportunity.model_copy(update={"stage": VentureStage.VALIDATING})
            self.store.save_opportunity(validating)
        self.store.append_event(
            AuditEvent(
                event_type="experiment.planned",
                entity_id=experiment.id,
                data={
                    "opportunity_id": str(experiment.opportunity_id),
                    "max_cost_cents": experiment.max_cost_cents,
                    "executable": executable,
                },
            )
        )

    def ranked_queue(self) -> list[RankedOpportunity]:
        opportunities = {item.id: item for item in self.store.list_opportunities()}
        output: list[RankedOpportunity] = []
        for scorecard in self.store.list_scorecards():
            opportunity = opportunities.get(scorecard.opportunity_id)
            if opportunity is not None:
                output.append(RankedOpportunity(opportunity=opportunity, scorecard=scorecard))
        return output
