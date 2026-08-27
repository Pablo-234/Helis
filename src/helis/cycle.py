from __future__ import annotations

from dataclasses import dataclass

from helis.analyst import OpportunityAnalyst
from helis.budget import BudgetExceeded, CycleBudget
from helis.engine import HelisEngine, RankedOpportunity
from helis.model_provider import ModelProvider
from helis.scout import OpportunityScout


@dataclass(slots=True)
class CycleReport:
    observations_used: int
    candidates_discovered: int
    candidates_evaluated: int
    budget_exhausted: bool
    ranked: list[RankedOpportunity]


class HelisCycle:
    """One bounded cognition cycle: observations -> candidates -> evidence-bound scoring."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget | None = None,
    ) -> None:
        self.engine = engine
        self.budget = budget or CycleBudget()
        self.scout = OpportunityScout(provider, self.budget)
        self.analyst = OpportunityAnalyst(provider, self.budget)

    def run(self, *, observation_limit: int = 100, candidate_limit: int = 5) -> CycleReport:
        observations = self.engine.store.list_observations(limit=observation_limit)
        if not observations:
            return CycleReport(0, 0, 0, False, self.engine.ranked_queue())

        candidates = self.scout.discover(observations)[:candidate_limit]
        for candidate in candidates:
            self.engine.ingest(candidate)

        evaluated = 0
        exhausted = False
        for candidate in candidates:
            try:
                assessment = self.analyst.assess(candidate)
            except BudgetExceeded:
                exhausted = True
                break
            self.engine.evaluate(candidate, assessment.dimensions)
            evaluated += 1

        return CycleReport(
            observations_used=len(observations),
            candidates_discovered=len(candidates),
            candidates_evaluated=evaluated,
            budget_exhausted=exhausted,
            ranked=self.engine.ranked_queue(),
        )
