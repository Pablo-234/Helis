from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from helis.budget import BudgetExceeded
from helis.domain import (
    Experiment,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentType,
    Opportunity,
    ValidationResult,
    utc_now,
)
from helis.engine import HelisEngine
from helis.policy import AutonomyPolicy
from helis.validation import rank_experiments


class ExperimentExecutor(Protocol):
    name: str

    def execute(
        self,
        experiment: Experiment,
        opportunity: Opportunity,
        run: ExperimentRun,
    ) -> ValidationResult: ...


@dataclass(slots=True)
class ValidationBudget:
    max_executions: int = 1
    max_cash_cents: float = 0.0
    max_duration_hours: int = 24
    executions: int = 0
    spent_cents: float = 0.0

    def allows(self, experiment: Experiment) -> bool:
        if self.executions >= self.max_executions:
            return False
        if self.spent_cents + experiment.max_cost_cents > self.max_cash_cents:
            return False
        return experiment.max_duration_hours <= self.max_duration_hours

    def record(self, result: ValidationResult) -> None:
        self.executions += 1
        self.spent_cents += result.actual_cost_cents


@dataclass(slots=True)
class ExecutionOutcome:
    run: ExperimentRun
    result: ValidationResult | None = None


class ValidationRunner:
    def __init__(
        self,
        engine: HelisEngine,
        policy: AutonomyPolicy,
        validation_budget: ValidationBudget | None = None,
        executors: dict[ExperimentType, ExperimentExecutor] | None = None,
    ) -> None:
        self.engine = engine
        self.policy = policy
        self.validation_budget = validation_budget or ValidationBudget()
        self.executors = executors or {}

    def execute_next(self, opportunity: Opportunity) -> ExecutionOutcome | None:
        experiments = self.engine.store.list_experiments(opportunity.id)
        reviews = rank_experiments(experiments, self.policy)

        for review in reviews:
            experiment = review.experiment
            runs = self.engine.store.list_experiment_runs(experiment_id=experiment.id)
            current = runs[0] if runs else None

            if current is not None and current.status in {
                ExperimentRunStatus.COMPLETED,
                ExperimentRunStatus.FAILED,
                ExperimentRunStatus.BLOCKED,
                ExperimentRunStatus.CANCELLED,
                ExperimentRunStatus.RUNNING,
                ExperimentRunStatus.WAITING_APPROVAL,
            }:
                continue

            if current is None:
                if review.requires_approval or not review.executable:
                    waiting = ExperimentRun(
                        experiment_id=experiment.id,
                        opportunity_id=opportunity.id,
                        status=ExperimentRunStatus.WAITING_APPROVAL,
                    )
                    self.engine.record_experiment_run(waiting, event_type="experiment.waiting_approval")
                    continue
                current = ExperimentRun(
                    experiment_id=experiment.id,
                    opportunity_id=opportunity.id,
                    status=ExperimentRunStatus.READY,
                )
                self.engine.record_experiment_run(current, event_type="experiment.ready")

            if not current.approval_granted and not review.executable:
                continue

            executor = self.executors.get(experiment.experiment_type)
            if executor is None:
                blocked = current.model_copy(
                    update={
                        "status": ExperimentRunStatus.BLOCKED,
                        "error": "adapter_not_configured",
                        "updated_at": utc_now(),
                    }
                )
                self.engine.record_experiment_run(blocked, event_type="experiment.blocked")
                continue

            if not self.validation_budget.allows(experiment):
                continue

            running = current.model_copy(
                update={
                    "status": ExperimentRunStatus.RUNNING,
                    "adapter": executor.name,
                    "started_at": utc_now(),
                    "error": None,
                    "updated_at": utc_now(),
                }
            )
            self.engine.record_experiment_run(running, event_type="experiment.started")
            try:
                result = executor.execute(experiment, opportunity, running)
            except BudgetExceeded:
                ready = running.model_copy(
                    update={
                        "status": ExperimentRunStatus.READY,
                        "error": "model_budget_exhausted",
                        "updated_at": utc_now(),
                    }
                )
                self.engine.record_experiment_run(ready, event_type="experiment.deferred")
                raise
            except Exception as exc:  # noqa: BLE001 -- executor failures are isolated here
                failed = running.model_copy(
                    update={
                        "status": ExperimentRunStatus.FAILED,
                        "completed_at": utc_now(),
                        "error": f"{type(exc).__name__}: {exc}",
                        "updated_at": utc_now(),
                    }
                )
                self.engine.record_experiment_run(failed, event_type="experiment.failed")
                return ExecutionOutcome(run=failed)

            self.engine.record_validation_result(result)
            completed = running.model_copy(
                update={
                    "status": ExperimentRunStatus.COMPLETED,
                    "completed_at": utc_now(),
                    "actual_cost_cents": result.actual_cost_cents,
                    "updated_at": utc_now(),
                }
            )
            self.engine.record_experiment_run(completed, event_type="experiment.completed")
            self.validation_budget.record(result)
            return ExecutionOutcome(run=completed, result=result)

        return None

    def approve(self, run_id: UUID) -> ExperimentRun:
        run = self.engine.store.get_experiment_run(run_id)
        if run is None:
            raise ValueError("experiment run not found")
        if run.status != ExperimentRunStatus.WAITING_APPROVAL:
            raise ValueError("only waiting-approval runs can be approved")
        approved = run.model_copy(
            update={
                "status": ExperimentRunStatus.READY,
                "approval_granted": True,
                "updated_at": utc_now(),
            }
        )
        self.engine.record_experiment_run(approved, event_type="experiment.approved")
        return approved
