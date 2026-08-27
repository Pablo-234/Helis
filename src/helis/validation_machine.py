from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from helis.budget import BudgetExceeded, CycleBudget
from helis.decision import VentureDecisionEngine
from helis.desk_research import DeskResearchExecutor
from helis.domain import (
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentType,
    ValidationResult,
    VentureDecision,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.policy import AutonomyPolicy
from helis.validation_execution import ExecutionOutcome, ValidationBudget, ValidationRunner


@dataclass(slots=True)
class ValidationTickReport:
    opportunity_id: UUID | None
    execution: ExecutionOutcome | None = None
    decision: VentureDecision | None = None
    waiting_approval: int = 0
    blocked: int = 0
    model_budget_exhausted: bool = False

    @property
    def result(self) -> ValidationResult | None:
        return self.execution.result if self.execution else None

    @property
    def run(self) -> ExperimentRun | None:
        return self.execution.run if self.execution else None


class ValidationMachine:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        model_budget: CycleBudget,
        policy: AutonomyPolicy | None = None,
        validation_budget: ValidationBudget | None = None,
    ) -> None:
        self.engine = engine
        self.model_budget = model_budget
        self.policy = policy or AutonomyPolicy()
        self.validation_budget = validation_budget or ValidationBudget()
        desk = DeskResearchExecutor(provider, model_budget, engine.store)
        self.runner = ValidationRunner(
            engine,
            self.policy,
            self.validation_budget,
            executors={ExperimentType.DESK_RESEARCH: desk},
        )
        self.decider = VentureDecisionEngine()

    def tick(self, opportunity_id: UUID | None = None) -> ValidationTickReport:
        target = self._target(opportunity_id)
        if target is None:
            return ValidationTickReport(opportunity_id=None)

        execution: ExecutionOutcome | None = None
        exhausted = False
        try:
            execution = self.runner.execute_next(target)
        except BudgetExceeded:
            exhausted = True

        decision = self.decide_if_changed(target.id)
        runs = self.engine.store.list_experiment_runs(opportunity_id=target.id)
        return ValidationTickReport(
            opportunity_id=target.id,
            execution=execution,
            decision=decision,
            waiting_approval=sum(
                item.status == ExperimentRunStatus.WAITING_APPROVAL for item in runs
            ),
            blocked=sum(item.status == ExperimentRunStatus.BLOCKED for item in runs),
            model_budget_exhausted=exhausted,
        )

    def decide_if_changed(self, opportunity_id: UUID) -> VentureDecision | None:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            return None
        results = self.engine.store.list_validation_results(opportunity_id)
        if not results:
            return None
        result_ids = {item.id for item in results}
        previous = self.engine.store.list_venture_decisions(opportunity_id)
        if previous and set(previous[0].result_ids) == result_ids:
            return None
        decision = self.decider.decide(
            opportunity,
            self.engine.store.list_experiments(opportunity_id),
            results,
        )
        self.engine.record_venture_decision(decision)
        return decision

    def _target(self, opportunity_id: UUID | None):
        if opportunity_id is not None:
            candidate = self.engine.store.get_opportunity(opportunity_id)
            if candidate is None or not self.engine.store.list_experiments(candidate.id):
                return None
            return candidate

        for item in self.engine.ranked_queue():
            if item.opportunity.stage not in {VentureStage.EVALUATED, VentureStage.VALIDATING}:
                continue
            if self.engine.store.list_experiments(item.opportunity.id):
                return item.opportunity
        return None
