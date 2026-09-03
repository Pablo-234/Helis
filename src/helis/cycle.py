from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from helis.analyst import OpportunityAnalyst
from helis.budget import BudgetExceeded, CycleBudget
from helis.domain import Opportunity, Recommendation, VentureStage
from helis.engine import HelisEngine, RankedOpportunity
from helis.experiment_designer import ExperimentDesigner
from helis.model_provider import ModelProvider
from helis.policy import AutonomyPolicy
from helis.scout import OpportunityScout
from helis.skeptic import VentureSkeptic
from helis.validation import ExperimentReview, rank_experiments


@dataclass(slots=True)
class CycleReport:
    observations_used: int
    candidates_discovered: int
    candidates_evaluated: int
    budget_exhausted: bool
    ranked: list[RankedOpportunity]
    observations_replayed: bool = False
    validation_opportunity_id: UUID | None = None
    validation_reviews: list[ExperimentReview] | None = None

    @property
    def experiments_planned(self) -> int:
        return len(self.validation_reviews or [])

    @property
    def executable_experiments(self) -> int:
        return sum(review.executable for review in self.validation_reviews or [])

    @property
    def approval_required_experiments(self) -> int:
        return sum(review.requires_approval for review in self.validation_reviews or [])


class HelisCycle:
    """One resumable bounded cycle: new evidence -> score -> falsify -> experiment plan."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget | None = None,
        policy: AutonomyPolicy | None = None,
        *,
        online_only: bool = False,
    ) -> None:
        self.engine = engine
        self.budget = budget or CycleBudget()
        self.policy = policy or AutonomyPolicy()
        self.scout = OpportunityScout(provider, self.budget, online_only=online_only)
        self.analyst = OpportunityAnalyst(provider, self.budget)
        self.skeptic = VentureSkeptic(provider, self.budget)
        self.experiment_designer = ExperimentDesigner(provider, self.budget)

    def run(self, *, observation_limit: int = 100, candidate_limit: int = 5) -> CycleReport:
        observations = self.engine.store.list_unprocessed_observations(limit=observation_limit)
        replayed = False
        if not observations and not self.engine.store.list_opportunities():
            observations = self.engine.store.list_observations(limit=observation_limit)
            replayed = bool(observations)
        generated_count = 0
        exhausted = False

        if observations:
            try:
                generated = self.scout.discover(observations)
            except BudgetExceeded:
                return CycleReport(
                    len(observations),
                    0,
                    0,
                    True,
                    self.engine.ranked_queue(),
                    observations_replayed=replayed,
                )
            generated_count = len(generated)
            for candidate in generated:
                self.engine.ingest(candidate)
            if generated:
                self.engine.store.mark_observations_processed(item.id for item in observations)

        pending = [
            opportunity
            for opportunity in self.engine.store.list_opportunities()
            if opportunity.stage == VentureStage.DISCOVERED
        ]
        evaluation_candidates = pending[:candidate_limit]
        evaluated = 0
        for candidate in evaluation_candidates:
            try:
                assessment = self.analyst.assess(candidate)
            except BudgetExceeded:
                exhausted = True
                break
            self.engine.evaluate(candidate, assessment.dimensions)
            evaluated += 1

        validation_target = self._next_validation_target()
        validation_reviews: list[ExperimentReview] = []
        if validation_target is not None and not exhausted:
            skeptic_report = self.engine.store.get_skeptic_report(validation_target.id)
            try:
                if skeptic_report is None:
                    skeptic_report = self.skeptic.review(validation_target)
                    self.engine.record_skeptic_report(skeptic_report)

                if not self.engine.store.list_experiments(validation_target.id):
                    experiments = self.experiment_designer.design(validation_target, skeptic_report)
                    validation_reviews = rank_experiments(experiments, self.policy)
                    for review in validation_reviews:
                        self.engine.plan_experiment(review.experiment, executable=review.executable)
            except BudgetExceeded:
                exhausted = True

        return CycleReport(
            observations_used=len(observations),
            candidates_discovered=generated_count,
            candidates_evaluated=evaluated,
            budget_exhausted=exhausted,
            ranked=self.engine.ranked_queue(),
            observations_replayed=replayed,
            validation_opportunity_id=validation_target.id if validation_target is not None else None,
            validation_reviews=validation_reviews,
        )

    def _next_validation_target(self) -> Opportunity | None:
        for item in self.engine.ranked_queue():
            if item.opportunity.stage != VentureStage.EVALUATED:
                continue
            if item.scorecard.recommendation == Recommendation.KILL:
                continue
            if self.engine.store.list_experiments(item.opportunity.id):
                continue
            return item.opportunity
        return None
