from __future__ import annotations

import pytest

from helis.domain import (
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.portfolio_value import VentureValueEstimator
from helis.resource_envelope import (
    EnvelopeConflict,
    EnvelopeExceeded,
    EnvelopeStatus,
    ResourceEnvelopeManager,
)
from helis.store import HelisStore


def _venture(engine: HelisEngine, title: str = "Envelope venture") -> Opportunity:
    opportunity = Opportunity(
        title=title,
        problem="A recurring service workflow creates enough customer pain to justify a bounded test.",
        customer="small service teams",
        proposed_value="reduce recurring workflow effort",
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=3),
            total=74,
            recommendation=Recommendation.VALIDATE,
            rationale=["fixture"],
        )
    )
    return opportunity


def _plan(engine: HelisEngine, *, cash: int, calls: int):
    return PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=cash,
            currency="PLN",
            model_calls=calls,
            reserve_fraction=0,
            max_concentration=1,
        )
    )


def test_new_plan_revokes_old_envelopes_and_stale_plan_cannot_reactivate(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    manager = ResourceEnvelopeManager(engine)

    first_plan = _plan(engine, cash=10_000, calls=4)
    first_envelope = manager.activate(first_plan)[0]
    assert first_envelope.status == EnvelopeStatus.ACTIVE

    second_plan = _plan(engine, cash=12_000, calls=5)
    second_envelope = manager.activate(second_plan)[0]

    assert manager.get(first_envelope.id).status == EnvelopeStatus.REVOKED
    assert second_envelope.status == EnvelopeStatus.ACTIVE
    with pytest.raises(EnvelopeConflict):
        manager.activate(first_plan)


def test_cash_consumption_is_idempotent_bounded_and_records_real_cost(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    manager = ResourceEnvelopeManager(engine)
    envelope = manager.activate(_plan(engine, cash=5_000, calls=2))[0]

    first = manager.consume(
        envelope.id,
        source="validation-gateway",
        idempotency_key="invoice-1",
        cash_cents=1_200,
    )
    second = manager.consume(
        envelope.id,
        source="validation-gateway",
        idempotency_key="invoice-1",
        cash_cents=1_200,
    )

    assert first.cash_consumed_cents == 1_200
    assert second.cash_consumed_cents == 1_200
    estimate = VentureValueEstimator(engine).estimate(opportunity.id, "PLN")
    assert estimate.observed_cost_cents == 1_200

    with pytest.raises(EnvelopeConflict):
        manager.consume(
            envelope.id,
            source="validation-gateway",
            idempotency_key="invoice-1",
            cash_cents=1_201,
        )
    with pytest.raises(EnvelopeExceeded):
        manager.consume(
            envelope.id,
            source="validation-gateway",
            idempotency_key="too-large",
            cash_cents=4_000,
        )


def test_model_budget_reserves_calls_before_external_request(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    manager = ResourceEnvelopeManager(engine)
    envelope = manager.activate(_plan(engine, cash=0, calls=2))[0]
    budget = manager.model_budget(envelope.id, max_tokens=1_000, max_model_cost_cents=10)

    # First attempt is reserved, then imagine the provider throws before budget.record().
    budget.ensure_call_available()
    assert manager.get(envelope.id).model_calls_consumed == 1
    assert budget.model_calls == 1

    # A retry consumes the second slot rather than silently reusing the failed attempt.
    budget.ensure_call_available()
    budget.record(ModelResult(content="{}", prompt_tokens=10, completion_tokens=5))
    assert manager.get(envelope.id).model_calls_consumed == 2
    assert budget.model_calls == 2

    with pytest.raises(EnvelopeExceeded):
        budget.ensure_call_available()


def test_envelope_becomes_exhausted_only_when_both_resource_limits_are_spent(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    manager = ResourceEnvelopeManager(engine)
    envelope = manager.activate(_plan(engine, cash=1_000, calls=1))[0]

    cash_only = manager.consume(
        envelope.id,
        source="experiment",
        idempotency_key="cash-1",
        cash_cents=1_000,
    )
    assert cash_only.status == EnvelopeStatus.ACTIVE
    assert cash_only.remaining_cash_cents == 0
    assert cash_only.remaining_model_calls == 1

    exhausted = manager.consume(
        envelope.id,
        source="model-call",
        idempotency_key="call-1",
        model_calls=1,
    )
    assert exhausted.status == EnvelopeStatus.EXHAUSTED
    assert exhausted.remaining_model_calls == 0
