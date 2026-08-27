from __future__ import annotations

from dataclasses import dataclass

from helis.domain import AuditEvent, Observation, Opportunity, Scorecard, ScoreDimensions, VentureStage
from helis.scoring import score_opportunity
from helis.store import HelisStore


@dataclass(slots=True)
class RankedOpportunity:
    opportunity: Opportunity
    scorecard: Scorecard


class HelisEngine:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.store.initialize()

    def observe(self, observation: Observation) -> Observation:
        self.store.save_observation(observation)
        self.store.append_event(
            AuditEvent(
                event_type="market.observed",
                entity_id=observation.id,
                data={"source": observation.source},
            )
        )
        return observation

    def ingest(self, opportunity: Opportunity) -> Opportunity:
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

        evaluated = opportunity.model_copy(update={"stage": VentureStage.EVALUATED})
        self.store.save_opportunity(evaluated)
        self.store.append_event(
            AuditEvent(
                event_type="opportunity.evaluated",
                entity_id=opportunity.id,
                data={
                    "score": scorecard.total,
                    "recommendation": scorecard.recommendation.value,
                },
            )
        )
        return scorecard

    def ranked_queue(self) -> list[RankedOpportunity]:
        opportunities = {item.id: item for item in self.store.list_opportunities()}
        output: list[RankedOpportunity] = []
        for scorecard in self.store.list_scorecards():
            opportunity = opportunities.get(scorecard.opportunity_id)
            if opportunity is not None:
                output.append(RankedOpportunity(opportunity=opportunity, scorecard=scorecard))
        return output
