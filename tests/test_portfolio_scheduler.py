from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from helis.cash_reservation import CashReservationManager
from helis.domain import (
    Experiment,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentType,
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.portfolio_scheduler import (
    PortfolioScheduler,
    SchedulerDisposition,
    SchedulerStore,
)
from helis.resource_envelope import ResourceEnvelopeManager
from helis.store import HelisStore


class NeverProvider:
    def complete(self, *, system: str, user: str) -> ModelResult:
        raise AssertionError("scheduler unit tests should use the injected fake runtime")


@dataclass(slots=True)
class FakeRuntime:
    envelope_id: UUID
    calls: list[tuple[UUID, float]]
    fail: bool = False

    def advance(self, *, validation_cash_cents: float = 0.0):
        self.calls.append((self.envelope_id, validation_cash_cents))
        if self.fail:
            raise RuntimeError("simulated venture runtime failure")
        return object()


def _opportunity(engine: HelisEngine, title: str, score: float) -> Opportunity:
    opportunity = Opportunity(
        title=title,
        problem="A recurring operational workflow consumes measurable staff time every week.",
        customer="small service teams",
        proposed_value="reduce repeated manual work",
        stage=VentureStage.VALIDATING,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(
                capital_efficiency=8 if score >= 80 else 6,
                execution_risk=2 if score >= 80 else 5,
            ),
            total=score,
            recommendation=Recommendation.VALIDATE,
            rationale=["scheduler fixture"],
        )
    )
    return opportunity


def _portfolio(tmp_path):
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    high = _opportunity(engine, "High priority venture", 88)
    low = _opportunity(engine, "Lower priority venture", 62)
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=10_000,
            currency="PLN",
            model_calls=20,
            reserve_fraction=0,
            max_ventures=4,
            max_concentration=0.70,
        )
    )
    envelopes = ResourceEnvelopeManager(engine)
    active = envelopes.activate(plan)
    by_opportunity = {item.opportunity_id: item for item in active}
    return engine, high, low, plan, envelopes, by_opportunity


def _scheduler(engine, calls, *, failing_envelope: UUID | None = None):
    def factory(envelope_id: UUID):
        return FakeRuntime(
            envelope_id,
            calls,
            fail=envelope_id == failing_envelope,
        )

    return PortfolioScheduler(
        engine,
        NeverProvider(),
        runtime_factory=factory,
    )


def test_scheduler_advances_highest_priority_and_caps_tick(tmp_path) -> None:
    engine, high, low, plan, _, by_opportunity = _portfolio(tmp_path)
    calls: list[tuple[UUID, float]] = []

    report = _scheduler(engine, calls).tick(max_advances=1)

    top = max(plan.allocations, key=lambda item: item.priority_score)
    assert top.opportunity_id == high.id
    assert calls[0][0] == by_opportunity[high.id].id
    assert report.advanced == 1
    assert report.attempted_advances == 1
    low_item = next(item for item in report.items if item.opportunity_id == low.id)
    assert low_item.disposition == SchedulerDisposition.SKIPPED
    assert low_item.reason == "tick_advance_cap"
    assert SchedulerStore(engine).latest().id == report.id


def test_waiting_approval_is_skipped_and_next_venture_advances(tmp_path) -> None:
    engine, high, low, _, _, by_opportunity = _portfolio(tmp_path)
    experiment = Experiment(
        opportunity_id=high.id,
        title="Approval-gated interview",
        experiment_type=ExperimentType.INTERVIEW,
        hypothesis="Customers confirm the operational pain.",
        success_metric="confirmed interviews",
        success_threshold=">= 3",
        requires_external_contact=True,
    )
    engine.plan_experiment(experiment, executable=False)
    engine.record_experiment_run(
        ExperimentRun(
            experiment_id=experiment.id,
            opportunity_id=high.id,
            status=ExperimentRunStatus.WAITING_APPROVAL,
        ),
        event_type="experiment.waiting_approval",
    )
    calls: list[tuple[UUID, float]] = []

    report = _scheduler(engine, calls).tick(max_advances=1)

    assert calls == [(by_opportunity[low.id].id, float(by_opportunity[low.id].cash_limit_cents))]
    high_item = next(item for item in report.items if item.opportunity_id == high.id)
    assert high_item.reason == "validation_waiting_approval"


def test_open_cash_commitment_is_skipped(tmp_path) -> None:
    engine, high, low, _, _, by_opportunity = _portfolio(tmp_path)
    high_envelope = by_opportunity[high.id]
    CashReservationManager(engine).reserve(
        high_envelope.id,
        amount_cents=100,
        source="test-commitment",
        idempotency_key="commitment-1",
    )
    calls: list[tuple[UUID, float]] = []

    report = _scheduler(engine, calls).tick(max_advances=1)

    assert calls[0][0] == by_opportunity[low.id].id
    high_item = next(item for item in report.items if item.opportunity_id == high.id)
    assert high_item.reason == "open_cash_commitment"


def test_no_model_capacity_is_skipped_without_consuming_tick_slot(tmp_path) -> None:
    engine, high, low, _, envelopes, by_opportunity = _portfolio(tmp_path)
    high_envelope = by_opportunity[high.id]
    envelopes.consume(
        high_envelope.id,
        source="test",
        idempotency_key="consume-high-model-calls",
        model_calls=high_envelope.model_call_limit,
    )
    calls: list[tuple[UUID, float]] = []

    report = _scheduler(engine, calls).tick(max_advances=1)

    assert calls[0][0] == by_opportunity[low.id].id
    high_item = next(item for item in report.items if item.opportunity_id == high.id)
    assert high_item.reason == "no_model_capacity"


def test_runtime_failure_is_isolated_and_counts_as_bounded_attempt(tmp_path) -> None:
    engine, high, low, _, _, by_opportunity = _portfolio(tmp_path)
    calls: list[tuple[UUID, float]] = []
    scheduler = _scheduler(
        engine,
        calls,
        failing_envelope=by_opportunity[high.id].id,
    )

    report = scheduler.tick(max_advances=1)

    assert report.failed == 1
    assert report.attempted_advances == 1
    assert calls == [
        (by_opportunity[high.id].id, float(by_opportunity[high.id].cash_limit_cents))
    ]
    low_item = next(item for item in report.items if item.opportunity_id == low.id)
    assert low_item.reason == "tick_advance_cap"
