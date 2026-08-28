from __future__ import annotations

from dataclasses import dataclass

from helis.cash_reservation import CashReservationManager
from helis.domain import (
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.portfolio import PortfolioAllocator, PortfolioBudget, PortfolioStore
from helis.portfolio_reallocation import (
    PortfolioReallocationStore,
    PortfolioReallocator,
    ReallocatingPortfolioControlLoop,
    ReallocationDisposition,
)
from helis.portfolio_scheduler import SchedulerTickReport
from helis.resource_envelope import EnvelopeStatus, ResourceEnvelopeManager
from helis.store import HelisStore


def _venture(engine: HelisEngine, title: str, score: float = 80) -> Opportunity:
    opportunity = Opportunity(
        title=title,
        problem="A recurring operational workflow wastes measurable staff time and money.",
        customer="small service teams",
        proposed_value="reduce repeated manual work",
        stage=VentureStage.VALIDATING,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(
                capital_efficiency=8,
                execution_risk=3,
            ),
            total=score,
            recommendation=Recommendation.VALIDATE,
            rationale=["reallocation fixture"],
        )
    )
    return opportunity


def _funded_portfolio(tmp_path):
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    first = _venture(engine, "First venture", 84)
    second = _venture(engine, "Second venture", 72)
    budget = PortfolioBudget(
        cash_cents=10_000,
        currency="PLN",
        model_calls=20,
        reserve_fraction=0,
        max_concentration=0.70,
    )
    plan = PortfolioAllocator(engine).plan(budget)
    envelopes = ResourceEnvelopeManager(engine)
    active = envelopes.activate(plan)
    return engine, first, second, plan, envelopes, active


def test_reconcile_activates_latest_funded_plan_if_needed(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine, "Funded venture")
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(cash_cents=5_000, model_calls=10, reserve_fraction=0)
    )

    report = PortfolioReallocator(engine).reconcile()

    assert report.disposition == ReallocationDisposition.ACTIVATED_EXISTING
    assert report.previous_plan_id == plan.id
    assert report.new_plan_id == plan.id
    assert report.activated_envelopes == len(plan.allocations)


def test_unchanged_state_keeps_same_plan(tmp_path) -> None:
    engine, _, _, plan, _, _ = _funded_portfolio(tmp_path)

    report = PortfolioReallocator(engine).reconcile()

    assert report.disposition == ReallocationDisposition.UNCHANGED
    assert report.previous_plan_id == plan.id
    assert report.new_plan_id == plan.id
    assert PortfolioStore(engine).latest().id == plan.id


def test_consumed_resources_are_not_restored_during_reallocation(tmp_path) -> None:
    engine, _, _, plan, envelopes, active = _funded_portfolio(tmp_path)
    target = active[0]
    envelopes.consume(
        target.id,
        source="test-spend",
        idempotency_key="spend-1200",
        cash_cents=1_200,
        model_calls=3,
    )

    report = PortfolioReallocator(engine).reconcile()
    latest = PortfolioStore(engine).latest()

    assert report.disposition == ReallocationDisposition.REALLOCATED
    assert report.previous_plan_id == plan.id
    assert latest.id == report.new_plan_id
    assert latest.id != plan.id
    assert report.cash_consumed_cents == 1_200
    assert report.remaining_cash_cents == 8_800
    assert report.model_calls_consumed == 3
    assert report.remaining_model_calls == 17
    assert latest.budget.cash_cents == 8_800
    assert latest.budget.model_calls == 17
    old = envelopes.get(target.id)
    assert old is not None and old.status == EnvelopeStatus.REVOKED
    assert all(
        item.plan_id == latest.id
        for item in envelopes.list(status=EnvelopeStatus.ACTIVE)
    )


def test_open_cash_commitment_defers_reallocation_without_creating_new_plan(tmp_path) -> None:
    engine, _, _, plan, _, active = _funded_portfolio(tmp_path)
    CashReservationManager(engine).reserve(
        active[0].id,
        amount_cents=500,
        source="external-test",
        idempotency_key="commitment-500",
    )
    opportunity = engine.store.get_opportunity(active[1].opportunity_id)
    assert opportunity is not None
    engine.store.save_opportunity(opportunity.model_copy(update={"stage": VentureStage.BUILDING}))

    report = PortfolioReallocator(engine).reconcile()

    assert report.disposition == ReallocationDisposition.DEFERRED_OPEN_COMMITMENT
    assert PortfolioStore(engine).latest().id == plan.id
    assert all(item.status == EnvelopeStatus.ACTIVE for item in active)


def test_candidate_state_change_reallocates_even_without_new_spend(tmp_path) -> None:
    engine, _, second, plan, _, _ = _funded_portfolio(tmp_path)
    engine.store.save_opportunity(second.model_copy(update={"stage": VentureStage.BUILDING}))

    report = PortfolioReallocator(engine).reconcile()
    latest = PortfolioStore(engine).latest()

    assert report.disposition == ReallocationDisposition.REALLOCATED
    assert latest.id != plan.id
    assert latest.budget.cash_cents == plan.budget.cash_cents
    assert latest.budget.model_calls == plan.budget.model_calls


def test_empty_plan_does_not_loop_activation_forever(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    dead = _venture(engine, "Killed venture", 95)
    engine.store.save_opportunity(dead.model_copy(update={"stage": VentureStage.KILLED}))
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(cash_cents=5_000, model_calls=10)
    )
    assert plan.allocations == []

    first = PortfolioReallocator(engine).reconcile()
    second = PortfolioReallocator(engine).reconcile()

    assert first.disposition == ReallocationDisposition.UNCHANGED
    assert second.disposition == ReallocationDisposition.UNCHANGED
    assert PortfolioStore(engine).latest().id == plan.id
    assert ResourceEnvelopeManager(engine).list() == []
    assert PortfolioReallocationStore(engine).latest().id == second.id


@dataclass(slots=True)
class InspectingScheduler:
    engine: HelisEngine
    seen_cash_budget: int | None = None
    seen_model_budget: int | None = None
    seen_active_plan_ids: set | None = None

    def tick(self, *, max_advances: int) -> SchedulerTickReport:
        latest = PortfolioStore(self.engine).latest()
        assert latest is not None
        self.seen_cash_budget = latest.budget.cash_cents
        self.seen_model_budget = latest.budget.model_calls
        self.seen_active_plan_ids = {
            item.plan_id
            for item in ResourceEnvelopeManager(self.engine).list(status=EnvelopeStatus.ACTIVE)
        }
        return SchedulerTickReport(plan_id=latest.id, max_advances=max_advances)


def test_control_loop_reconciles_before_scheduler_runs(tmp_path) -> None:
    engine, _, _, _, envelopes, active = _funded_portfolio(tmp_path)
    envelopes.consume(
        active[0].id,
        source="test-use",
        idempotency_key="use-before-control-loop",
        cash_cents=700,
        model_calls=2,
    )
    scheduler = InspectingScheduler(engine)

    report = ReallocatingPortfolioControlLoop(engine, scheduler).tick(max_advances=2)
    latest = PortfolioStore(engine).latest()

    assert scheduler.seen_cash_budget == 9_300
    assert scheduler.seen_model_budget == 18
    assert scheduler.seen_active_plan_ids == {latest.id}
    assert report.plan_id == latest.id
