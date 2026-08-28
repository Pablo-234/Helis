from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from helis.budget import BudgetExceeded
from helis.domain import (
    AuditEvent,
    Experiment,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentType,
    ExternalDispatch,
    Opportunity,
    ValidationResult,
    utc_now,
)
from helis.engine import HelisEngine
from helis.policy import AutonomyPolicy
from helis.resource_envelope import EnvelopeConflict, EnvelopeExceeded
from helis.validation import rank_experiments
from helis.validation_cash import ValidationCashCoordinator


class ExperimentExecutor(Protocol):
    name: str

    def execute(
        self,
        experiment: Experiment,
        opportunity: Opportunity,
        run: ExperimentRun,
    ) -> ValidationResult | ExternalDispatch: ...


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

    def record_result(self, result: ValidationResult) -> None:
        self.executions += 1
        self.spent_cents += result.actual_cost_cents

    def record_dispatch(self, experiment: Experiment) -> None:
        self.executions += 1
        self.spent_cents += experiment.max_cost_cents


@dataclass(slots=True)
class ExecutionOutcome:
    run: ExperimentRun
    result: ValidationResult | None = None
    dispatch: ExternalDispatch | None = None


class ValidationRunner:
    def __init__(
        self,
        engine: HelisEngine,
        policy: AutonomyPolicy,
        validation_budget: ValidationBudget | None = None,
        executors: dict[ExperimentType, ExperimentExecutor] | None = None,
        cash_envelope_id: UUID | None = None,
    ) -> None:
        self.engine = engine
        self.policy = policy
        self.validation_budget = validation_budget or ValidationBudget()
        self.executors = executors or {}
        self.cash = ValidationCashCoordinator(engine, cash_envelope_id)

    def execute_next(self, opportunity: Opportunity) -> ExecutionOutcome | None:
        experiments = self.engine.store.list_experiments(opportunity.id)
        reviews = rank_experiments(experiments, self.policy)

        for review in reviews:
            experiment = review.experiment
            executor = self.executors.get(experiment.experiment_type)
            executor_requires_approval = bool(
                getattr(executor, "requires_run_approval", False)
            )
            executor_requires_cash = bool(
                getattr(executor, "requires_cash_reservation", False)
            )
            runs = self.engine.store.list_experiment_runs(experiment_id=experiment.id)
            current = runs[0] if runs else None

            if current is not None and current.status in {
                ExperimentRunStatus.COMPLETED,
                ExperimentRunStatus.FAILED,
                ExperimentRunStatus.BLOCKED,
                ExperimentRunStatus.CANCELLED,
                ExperimentRunStatus.RUNNING,
                ExperimentRunStatus.WAITING_RESULT,
            }:
                continue

            needs_approval = (
                review.requires_approval or not review.executable or executor_requires_approval
            )
            if current is None:
                if needs_approval:
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

            if current.status == ExperimentRunStatus.WAITING_APPROVAL:
                continue
            if needs_approval and not current.approval_granted:
                waiting = current.model_copy(
                    update={
                        "status": ExperimentRunStatus.WAITING_APPROVAL,
                        "updated_at": utc_now(),
                    }
                )
                self.engine.record_experiment_run(waiting, event_type="experiment.waiting_approval")
                continue

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

            if executor_requires_cash:
                try:
                    self.cash.reserve_for_run(running, experiment)
                except (EnvelopeConflict, EnvelopeExceeded) as exc:
                    ready = running.model_copy(
                        update={
                            "status": ExperimentRunStatus.READY,
                            "error": f"cash_reservation_failed: {exc}",
                            "updated_at": utc_now(),
                        }
                    )
                    self.engine.record_experiment_run(
                        ready,
                        event_type="experiment.deferred_cash",
                    )
                    return ExecutionOutcome(run=ready)

            try:
                execution = executor.execute(experiment, opportunity, running)
            except BudgetExceeded:
                if executor_requires_cash:
                    self.cash.release_for_run(
                        running.id,
                        reason="model budget exhausted before successful external dispatch",
                    )
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
                if executor_requires_cash:
                    self.cash.release_for_run(
                        running.id,
                        reason="external executor failed before successful dispatch",
                    )
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

            if isinstance(execution, ExternalDispatch):
                waiting_result = running.model_copy(
                    update={
                        "status": ExperimentRunStatus.WAITING_RESULT,
                        "external_ref": execution.dispatch_id,
                        "updated_at": utc_now(),
                    }
                )
                self.engine.record_experiment_run(
                    waiting_result,
                    event_type="experiment.dispatched",
                )
                self.validation_budget.record_dispatch(experiment)
                return ExecutionOutcome(run=waiting_result, dispatch=execution)

            if executor_requires_cash:
                self._settle_cash_or_raise_overrun(
                    running,
                    experiment,
                    execution.actual_cost_cents,
                )
            self.engine.record_validation_result(execution)
            completed = running.model_copy(
                update={
                    "status": ExperimentRunStatus.COMPLETED,
                    "completed_at": utc_now(),
                    "actual_cost_cents": execution.actual_cost_cents,
                    "updated_at": utc_now(),
                }
            )
            self.engine.record_experiment_run(completed, event_type="experiment.completed")
            self.validation_budget.record_result(execution)
            return ExecutionOutcome(run=completed, result=execution)

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

    def complete_external(self, result: ValidationResult) -> ExperimentRun:
        run = self.engine.store.get_experiment_run(result.run_id)
        if run is None:
            raise ValueError("experiment run not found")
        if run.status != ExperimentRunStatus.WAITING_RESULT:
            raise ValueError("external results are accepted only for waiting-result runs")
        if run.experiment_id != result.experiment_id or run.opportunity_id != result.opportunity_id:
            raise ValueError("validation result identifiers do not match the dispatched run")
        if any(
            existing.run_id == result.run_id
            for existing in self.engine.store.list_validation_results(run.opportunity_id)
        ):
            raise ValueError("a validation result for this run has already been recorded")

        experiment = self.engine.store.get_experiment(run.experiment_id)
        if experiment is None:
            raise ValueError("experiment not found")

        reservation = self.cash.find_for_run(run.id)
        if reservation is not None:
            self._settle_cash_or_raise_overrun(
                run,
                experiment,
                result.actual_cost_cents,
            )

        self.engine.record_validation_result(result)
        completed = run.model_copy(
            update={
                "status": ExperimentRunStatus.COMPLETED,
                "completed_at": utc_now(),
                "actual_cost_cents": result.actual_cost_cents,
                "error": None,
                "updated_at": utc_now(),
            }
        )
        self.engine.record_experiment_run(completed, event_type="experiment.completed_external")
        if reservation is None and result.actual_cost_cents > experiment.max_cost_cents:
            self._record_cost_overrun(run, experiment, result.actual_cost_cents)
        return completed

    def _settle_cash_or_raise_overrun(
        self,
        run: ExperimentRun,
        experiment: Experiment,
        actual_cost_cents: float,
    ) -> None:
        reservation = self.cash.find_for_run(run.id)
        if reservation is None:
            if actual_cost_cents > 0:
                self._record_cost_overrun(run, experiment, actual_cost_cents)
                raise EnvelopeExceeded("paid validation result has no cash reservation")
            return
        if actual_cost_cents > reservation.reserved_cents:
            self._record_cost_overrun(run, experiment, actual_cost_cents)
            raise EnvelopeExceeded("validation actual cost exceeds reserved cash")
        self.cash.settle_for_run(run.id, actual_cost_cents=actual_cost_cents)

    def _record_cost_overrun(
        self,
        run: ExperimentRun,
        experiment: Experiment,
        actual_cost_cents: float,
    ) -> None:
        self.engine.store.append_event(
            AuditEvent(
                event_type="experiment.cost_overrun",
                entity_id=run.id,
                data={
                    "planned_max_cost_cents": experiment.max_cost_cents,
                    "actual_cost_cents": actual_cost_cents,
                },
            )
        )
