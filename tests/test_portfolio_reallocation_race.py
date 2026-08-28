from __future__ import annotations

import sqlite3

import pytest

from helis.domain import (
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.portfolio import PortfolioAllocator, PortfolioBudget, PortfolioStore
from helis.portfolio_reallocation import PortfolioReallocator, ReallocationDisposition
from helis.resource_envelope import EnvelopeStatus, ResourceEnvelopeManager
from helis.store import HelisStore


def _venture(engine: HelisEngine, title: str, score: float) -> Opportunity:
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
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=3),
            total=score,
            recommendation=Recommendation.VALIDATE,
            rationale=["race fixture"],
        )
    )
    return opportunity


def _funded(tmp_path):
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    first = _venture(engine, "First venture", 84)
    _venture(engine, "Second venture", 72)
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=10_000,
            currency="PLN",
            model_calls=20,
            reserve_fraction=0,
            max_concentration=0.70,
        )
    )
    ResourceEnvelopeManager(engine).activate(plan)
    return engine, first, plan


def test_commitment_race_rolls_back_unactivated_latest_plan(tmp_path, monkeypatch) -> None:
    engine, first, original = _funded(tmp_path)
    engine.store.save_opportunity(first.model_copy(update={"stage": VentureStage.KILLED}))
    reallocator = PortfolioReallocator(engine)

    def fail_activation(plan):
        raise sqlite3.IntegrityError("cannot revoke envelope with open cash reservation")

    monkeypatch.setattr(reallocator.envelopes, "activate", fail_activation)
    report = reallocator.reconcile()

    assert report.disposition == ReallocationDisposition.DEFERRED_OPEN_COMMITMENT
    assert report.new_plan_id == original.id
    assert PortfolioStore(engine).latest().id == original.id
    active = ResourceEnvelopeManager(engine).list(status=EnvelopeStatus.ACTIVE)
    assert active and all(item.plan_id == original.id for item in active)


def test_unrelated_activation_integrity_error_is_not_hidden(tmp_path, monkeypatch) -> None:
    engine, first, _ = _funded(tmp_path)
    engine.store.save_opportunity(first.model_copy(update={"stage": VentureStage.KILLED}))
    reallocator = PortfolioReallocator(engine)

    def fail_activation(plan):
        raise sqlite3.IntegrityError("unrelated unique constraint failure")

    monkeypatch.setattr(reallocator.envelopes, "activate", fail_activation)

    with pytest.raises(sqlite3.IntegrityError, match="unrelated"):
        reallocator.reconcile()
