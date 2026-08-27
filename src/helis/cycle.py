from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from helis.analyst import OpportunityAnalyst
from helis.budget import BudgetExceeded, CycleBudget
from helis.domain import Recommendation
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
    """One bounded venture cycle: evidence -> candidates -> score -> falsify -> experiments."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget | None = None,
        policy: AutonomyPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.budget = budget or CycleBudget()
        self.policy = policy or AutonomyPolicy()
        self.scout = OpportunityScout(provider, self.budget)
        self.analyst = OpportunityAnalyst(provider, self.budget)
        self.skeptic = VentureSkeptic(provider, self.budget)
        self.experiment_designer = ExperimentDesigner(provider, self.budget)

    def run(self, *, observation_limit: int = 100, candidate_limit: int = 5) -> CycleReport:
        observations = self.engine.store.list_observations(limit=observation_limit)
        if not observations:
            return CycleReport(0, 0, 0, False, self.engine.ranked_queue())

        candidates = self.scout.discover(observations)[:candidate_limit]
        for candidate in candidates:
            self.engine.ingest(candidate)

        evaluated = 0
        exhausted = False
        local_scores: list[tuple[float, Recommendation, object]] = []
        for candidate in candidates:
            try:
                assessment = self.analyst.assess(candidate)
            except BudgetExceeded:
                exhausted = True
                break
            scorecard = self.engine.evaluate(candidate, assessment.dimensions)
            local_scores.append((scorecard.total, scorecard.recommendation, candidate))
            evaluated += 1

        validation_target = None
        validation_reviews: list[ExperimentReview] = []
        viable = [item for item in local_scores if item[1] != Recommendation.KILL]
        if viable and not exhausted:
            _, _, validation_target = max(viable, key=lambda item: item[0])
            try:
                skeptic_report = self.skeptic.review(validation_target)
                self.engine.record_skeptic_report(skeptic_report)
                experiments = self.experiment_designer.design(validation_target, skeptic_report)
                validation_reviews = rank_experiments(experiments, self.policy)
                for review in validation_reviews:
                    self.engine.plan_experiment(review.experiment, executable=review.executable)
            except BudgetExceeded:
                exhausted = True

        return CycleReport(
            observations_used=len(observations),
            candidates_discovered=len(candidates),
            candidates_evaluated=evaluated,
            budget_exhausted=exhausted,
            ranked=self.engine.ranked_queue(),
            validation_opportunity_id=validation_target.id if validation_target is not None else None,
            validation_reviews=validation_reviews,
        )
