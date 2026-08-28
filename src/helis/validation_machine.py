from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from helis.budget import BudgetExceeded, CycleBudget
from helis.decision import VentureDecisionEngine
from helis.desk_research import DeskResearchExecutor
from helis.domain import (
    Experiment,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentType,
    ValidationResult,
    VentureDecision,
    VentureDecisionKind,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.followup import FollowUpDesigner
from helis.model_provider import ModelProvider
from helis.policy import AutonomyPolicy
from helis.validation import review_experiment
from helis.validation_execution import ExecutionOutcome, ValidationBudget, ValidationRunner
from helis.validation_gateway import ApprovedValidationGateway


@dataclass(slots=True)
class ValidationTickReport:
    opportunity_id: UUID | None
    execution: ExecutionOutcome | None = None
    decision: VentureDecision | None = None
    follow_up_planned: Experiment | None = None
    waiting_approval: int = 0
    waiting_result: int = 0
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
        external_gateway: ApprovedValidationGateway | None = None,
        cash_envelope_id: UUID | None = None,
    ) -> None:
        self.engine = engine
        self.model_budget = model_budget
        self.policy = policy or AutonomyPolicy()
        self.validation_budget = validation_budget or ValidationBudget()
        desk = DeskResearchExecutor(provider, model_budget, engine.store)
        executors = {ExperimentType.DESK_RESEARCH: desk}
        if external_gateway is not None:
            executors[ExperimentType.INTERVIEW] = external_gateway
            executors[ExperimentType.PRICING] = external_gateway
        self.runner = ValidationRunner(
            engine,
            self.policy,
            self.validation_budget,
            executors=executors,
            cash_envelope_id=cash_envelope_id,
        )
        self.decider = VentureDecisionEngine()
        self.follow_up_designer = FollowUpDesigner(provider, model_budget)

    def tick(self, opportunity_id: UUID | None = None) -> ValidationTickReport:
        target = self._target(opportunity_id)
        if target is None:
            return ValidationTickReport(opportunity_id=None)

        prior_decision = self.decide_if_changed(target.id)
        effective_prior = prior_decision
        if effective_prior is None:
            previous = self.engine.store.list_venture_decisions(target.id)
            effective_prior = previous[0] if previous else None
        if effective_prior is not None and effective_prior.decision != VentureDecisionKind.CONTINUE:
            return self._report(target.id, decision=prior_decision)

        execution: ExecutionOutcome | None = None
        exhausted = False
        try:
            execution = self.runner.execute_next(target)
        except BudgetExceeded:
            exhausted = True

        post_decision = self.decide_if_changed(target.id)
        decision = post_decision or prior_decision
        effective_decision = decision
        if effective_decision is None:
            previous = self.engine.store.list_venture_decisions(target.id)
            effective_decision = previous[0] if previous else None

        follow_up: Experiment | None = None
        if (
            not exhausted
            and effective_decision is not None
            and effective_decision.decision == VentureDecisionKind.CONTINUE
        ):
            try:
                follow_up = self._plan_follow_up_if_needed(target.id)
            except BudgetExceeded:
                exhausted = True

        return self._report(
            target.id,
            execution=execution,
            decision=decision,
            follow_up=follow_up,
            exhausted=exhausted,
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

    def _report(
        self,
        opportunity_id: UUID,
        *,
        execution: ExecutionOutcome | None = None,
        decision: VentureDecision | None = None,
        follow_up: Experiment | None = None,
        exhausted: bool = False,
    ) -> ValidationTickReport:
        runs = self.engine.store.list_experiment_runs(opportunity_id=opportunity_id)
        return ValidationTickReport(
            opportunity_id=opportunity_id,
            execution=execution,
            decision=decision,
            follow_up_planned=follow_up,
            waiting_approval=sum(
                item.status == ExperimentRunStatus.WAITING_APPROVAL for item in runs
            ),
            waiting_result=sum(
                item.status == ExperimentRunStatus.WAITING_RESULT for item in runs
            ),
            blocked=sum(item.status == ExperimentRunStatus.BLOCKED for item in runs),
            model_budget_exhausted=exhausted,
        )

    def _plan_follow_up_if_needed(self, opportunity_id: UUID) -> Experiment | None:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            return None
        results = self.engine.store.list_validation_results(opportunity_id)
        if not results:
            return None
        experiments = self.engine.store.list_experiments(opportunity_id)
        runs = self.engine.store.list_experiment_runs(opportunity_id=opportunity_id)
        latest_by_experiment: dict[UUID, ExperimentRun] = {}
        for run in runs:
            latest_by_experiment.setdefault(run.experiment_id, run)

        active_statuses = {
            ExperimentRunStatus.PLANNED,
            ExperimentRunStatus.READY,
            ExperimentRunStatus.RUNNING,
            ExperimentRunStatus.WAITING_APPROVAL,
            ExperimentRunStatus.WAITING_RESULT,
        }
        if any(run.status in active_statuses for run in latest_by_experiment.values()):
            return None
        if any(item.id not in latest_by_experiment for item in experiments):
            return None

        latest_result_at = max(item.created_at for item in results)
        if any(item.created_at > latest_result_at for item in experiments):
            return None

        experiment = self.follow_up_designer.design(opportunity, experiments, results)
        if experiment is None:
            return None
        review = review_experiment(experiment, self.policy)
        self.engine.plan_experiment(experiment, executable=review.executable)
        return experiment

    def _target(self, opportunity_id: UUID | None):
        allowed_stages = {VentureStage.EVALUATED, VentureStage.VALIDATING}
        if opportunity_id is not None:
            candidate = self.engine.store.get_opportunity(opportunity_id)
            if (
                candidate is None
                or candidate.stage not in allowed_stages
                or not self.engine.store.list_experiments(candidate.id)
            ):
                return None
            return candidate

        for item in self.engine.ranked_queue():
            if item.opportunity.stage not in allowed_stages:
                continue
            if self.engine.store.list_experiments(item.opportunity.id):
                return item.opportunity
        return None
