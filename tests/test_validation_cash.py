from __future__ import annotations

from dataclasses import dataclass

import pytest

from helis.cash_reservation import CashReservationManager, CashReservationStatus
from helis.domain import (
    Experiment,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentType,
    ExternalDispatch,
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    ValidationOutcome,
    ValidationResult,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.policy import AutonomyPolicy
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.portfolio_value import VentureValueEstimator
from helis.resource_envelope import EnvelopeExceeded, ResourceEnvelopeManager
from helis.store import HelisStore
from helis.validation_cash import ValidationCashCoordinator
from helis.validation_execution import ValidationBudget, ValidationRunner


@dataclass(slots=True)
class CashDispatchExecutor:
    engine: HelisEngine
    envelope_id: object
    should_fail: bool = False
    name: str = "cash_dispatch_test"
    requires_cash_reservation: bool = True
    requires_run_approval: bool = False
    calls: int = 0
    available_cash_seen_inside_execute: int | None = None

    def execute(
        self,
        experiment: Experiment,
        opportunity: Opportunity,
        run: ExperimentRun,
    ) -> ExternalDispatch:
        self.calls += 1
        self.available_cash_seen_inside_execute = CashReservationManager(
            self.engine
        ).available_cash(self.envelope_id)
        if self.should_fail:
            raise RuntimeError("transport failed before dispatch acknowledgement")
        return ExternalDispatch(dispatch_id=f"dispatch-{run.id}", channel=self.name)


def _venture(engine: HelisEngine) -> Opportunity:
    opportunity = Opportunity(
        title="Automatic paid validation",
        problem="A recurring workflow problem needs a small paid market validation experiment.",
        customer="small service teams",
        proposed_value="reduce recurring manual work",
        stage=VentureStage.VALIDATING,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=3),
            total=78,
            recommendation=Recommendation.VALIDATE,
            rationale=["fixture"],
        )
    )
    return opportunity


def _envelope(engine: HelisEngine, *, cash_cents: int = 1_000):
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=cash_cents,
            currency="PLN",
            model_calls=2,
            reserve_fraction=0,
            max_concentration=1,
        )
    )
    return ResourceEnvelopeManager(engine).activate(plan)[0]


def _experiment(engine: HelisEngine, opportunity: Opportunity, *, max_cost_cents: int = 500):
    experiment = Experiment(
        opportunity_id=opportunity.id,
        title="Run bounded paid validation",
        experiment_type=ExperimentType.PRICING,
        hypothesis="At least one target customer accepts the bounded paid validation offer.",
        success_metric="accepted paid validation offers",
        success_threshold=">= 1",
        max_cost_cents=max_cost_cents,
    )
    engine.plan_experiment(experiment, executable=True)
    return experiment


def _runner(
    engine: HelisEngine,
    executor: CashDispatchExecutor,
    *,
    envelope_id=None,
    cash_limit: int = 1_000,
) -> ValidationRunner:
    return ValidationRunner(
        engine,
        AutonomyPolicy(autonomous_spend_limit_cents=cash_limit),
        ValidationBudget(max_executions=1, max_cash_cents=cash_limit),
        executors={ExperimentType.PRICING: executor},
        cash_envelope_id=envelope_id,
    )


def test_paid_external_validation_reserves_before_executor_and_settles_actual_cost(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    envelope = _envelope(engine)
    experiment = _experiment(engine, opportunity)
    executor = CashDispatchExecutor(engine, envelope.id)
    runner = _runner(engine, executor, envelope_id=envelope.id)

    outcome = runner.execute_next(opportunity)

    assert outcome is not None and outcome.dispatch is not None
    assert outcome.run.status == ExperimentRunStatus.WAITING_RESULT
    assert executor.calls == 1
    assert executor.available_cash_seen_inside_execute == 500

    reservation = ValidationCashCoordinator(engine).find_for_run(outcome.run.id)
    assert reservation is not None
    assert reservation.status == CashReservationStatus.RESERVED
    assert reservation.reserved_cents == 500
    assert VentureValueEstimator(engine).estimate(opportunity.id, "PLN").observed_cost_cents == 0

    result = ValidationResult(
        run_id=outcome.run.id,
        experiment_id=experiment.id,
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.POSITIVE,
        confidence=0.8,
        summary="The paid test produced a positive signal.",
        source="cash_dispatch_test",
        actual_cost_cents=180,
    )
    completed = runner.complete_external(result)

    assert completed.status == ExperimentRunStatus.COMPLETED
    settled = ValidationCashCoordinator(engine).find_for_run(outcome.run.id)
    assert settled is not None
    assert settled.status == CashReservationStatus.SETTLED
    assert settled.settled_cents == 180
    assert CashReservationManager(engine).available_cash(envelope.id) == 820
    assert VentureValueEstimator(engine).estimate(opportunity.id, "PLN").observed_cost_cents == 180


def test_executor_failure_releases_reserved_cash(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    envelope = _envelope(engine)
    _experiment(engine, opportunity)
    executor = CashDispatchExecutor(engine, envelope.id, should_fail=True)
    runner = _runner(engine, executor, envelope_id=envelope.id)

    outcome = runner.execute_next(opportunity)

    assert outcome is not None
    assert outcome.run.status == ExperimentRunStatus.FAILED
    reservation = ValidationCashCoordinator(engine).find_for_run(outcome.run.id)
    assert reservation is not None
    assert reservation.status == CashReservationStatus.RELEASED
    assert CashReservationManager(engine).available_cash(envelope.id) == 1_000
    assert VentureValueEstimator(engine).estimate(opportunity.id, "PLN").observed_cost_cents == 0


def test_paid_executor_without_envelope_is_deferred_before_side_effect(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    _experiment(engine, opportunity)
    executor = CashDispatchExecutor(engine, "unused")
    runner = _runner(engine, executor, envelope_id=None)

    outcome = runner.execute_next(opportunity)

    assert outcome is not None
    assert outcome.run.status == ExperimentRunStatus.READY
    assert outcome.run.error is not None and "cash_reservation_failed" in outcome.run.error
    assert executor.calls == 0


def test_external_cost_overrun_is_audited_and_not_silently_settled(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    envelope = _envelope(engine)
    experiment = _experiment(engine, opportunity, max_cost_cents=500)
    executor = CashDispatchExecutor(engine, envelope.id)
    runner = _runner(engine, executor, envelope_id=envelope.id)
    dispatched = runner.execute_next(opportunity)
    assert dispatched is not None and dispatched.dispatch is not None

    result = ValidationResult(
        run_id=dispatched.run.id,
        experiment_id=experiment.id,
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.POSITIVE,
        confidence=0.8,
        summary="The external callback reported a cost above the approved reservation.",
        source="cash_dispatch_test",
        actual_cost_cents=501,
    )

    with pytest.raises(EnvelopeExceeded, match="exceeds reserved cash"):
        runner.complete_external(result)

    reservation = ValidationCashCoordinator(engine).find_for_run(dispatched.run.id)
    assert reservation is not None and reservation.status == CashReservationStatus.RESERVED
    assert engine.store.list_validation_results(opportunity.id) == []
    persisted = engine.store.get_experiment_run(dispatched.run.id)
    assert persisted is not None and persisted.status == ExperimentRunStatus.WAITING_RESULT
    with engine.store.connect() as db:
        row = db.execute(
            "SELECT COUNT(*) AS count FROM events WHERE event_type = ? AND entity_id = ?",
            ("experiment.cost_overrun", str(dispatched.run.id)),
        ).fetchone()
    assert row["count"] == 1
