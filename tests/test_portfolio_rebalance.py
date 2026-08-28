from __future__ import annotations

import sqlite3

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
from helis.portfolio_rebalance import PortfolioRebalancer, RebalanceDisposition
from helis.resource_envelope import EnvelopeStatus, ResourceEnvelopeManager
from helis.store import HelisStore


def _venture(engine: HelisEngine, title: str, score: float) -> Opportunity:
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
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=3),
            total=score,
            recommendation=Recommendation.VALIDATE,
            rationale=["rebalance fixture"],
        )
    )
    return opportunity


def _portfolio(tmp_path):
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    first = _venture(engine, "First venture", 88)
    second = _venture(engine, "Second venture", 72)
    budget = PortfolioBudget(
        cash_cents=10_000,
        currency="PLN",
        model_calls=20,
        reserve_fraction=0,
        max_ventures=4,
        max_concentration=0.70,
    )
    plan = PortfolioAllocator(engine).plan(budget)
    ResourceEnvelopeManager(engine).activate(plan)
    return engine, first, second, plan


def test_unchanged_snapshot_keeps_current_plan(tmp_path) -> None:
    engine, _, _, plan = _portfolio(tmp_path)

    result = PortfolioRebalancer(engine).rebalance()

    assert result.disposition == RebalanceDisposition.UNCHANGED
    assert result.plan_id == plan.id
    assert PortfolioStore(engine).latest().id == plan.id
    active = ResourceEnvelopeManager(engine).list(status=EnvelopeStatus.ACTIVE)
    assert active and all(item.plan_id == plan.id for item in active)


def test_stage_change_rebalances_and_activates_new_envelopes(tmp_path) -> None:
    engine, first, second, plan = _portfolio(tmp_path)
    engine.store.save_opportunity(first.model_copy(update={"stage": VentureStage.KILLED}))

    result = PortfolioRebalancer(engine).rebalance()

    assert result.disposition == RebalanceDisposition.REBALANCED
    assert result.previous_plan_id == plan.id
    assert result.plan_id != plan.id
    latest = PortfolioStore(engine).latest()
    assert latest is not None and latest.id == result.plan_id
    active = ResourceEnvelopeManager(engine).list(status=EnvelopeStatus.ACTIVE)
    assert active and all(item.plan_id == latest.id for item in active)
    assert all(item.opportunity_id == second.id for item in active)


def test_open_cash_commitment_blocks_replan_before_new_latest_plan(tmp_path) -> None:
    engine, first, _, plan = _portfolio(tmp_path)
    active = ResourceEnvelopeManager(engine).list(status=EnvelopeStatus.ACTIVE)
    envelope = next(item for item in active if item.opportunity_id == first.id)
    CashReservationManager(engine).reserve(
        envelope.id,
        amount_cents=100,
        source="test",
        idempotency_key="open-commitment",
    )
    engine.store.save_opportunity(first.model_copy(update={"stage": VentureStage.KILLED}))

    result = PortfolioRebalancer(engine).rebalance()

    assert result.disposition == RebalanceDisposition.BLOCKED
    assert PortfolioStore(engine).latest().id == plan.id
    still_active = ResourceEnvelopeManager(engine).list(status=EnvelopeStatus.ACTIVE)
    assert still_active and all(item.plan_id == plan.id for item in still_active)


def test_activation_race_discards_unactivated_new_plan(tmp_path, monkeypatch) -> None:
    engine, first, _, plan = _portfolio(tmp_path)
    engine.store.save_opportunity(first.model_copy(update={"stage": VentureStage.KILLED}))
    rebalancer = PortfolioRebalancer(engine)

    def fail_activation(candidate):
        raise sqlite3.IntegrityError("simulated concurrent open cash reservation")

    monkeypatch.setattr(rebalancer.envelopes, "activate", fail_activation)
    result = rebalancer.rebalance()

    assert result.disposition == RebalanceDisposition.BLOCKED
    assert PortfolioStore(engine).latest().id == plan.id
