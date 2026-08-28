from __future__ import annotations

import sqlite3

import pytest

from helis.cash_reservation import CashReservationManager, CashReservationStatus
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
from helis.resource_envelope import EnvelopeConflict, EnvelopeExceeded, ResourceEnvelopeManager
from helis.store import HelisStore
from helis.venture_runtime import VentureRuntime


class NoopProvider:
    def complete(self, *, system: str, user: str) -> ModelResult:
        raise AssertionError("model provider must not be reached")


def _venture(engine: HelisEngine) -> Opportunity:
    opportunity = Opportunity(
        title="Cash reservation venture",
        problem="A recurring service workflow creates enough pain to justify a bounded paid test.",
        customer="small service teams",
        proposed_value="reduce recurring workflow cost",
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=3),
            total=76,
            recommendation=Recommendation.VALIDATE,
            rationale=["fixture"],
        )
    )
    return opportunity


def _plan(engine: HelisEngine, *, cash_cents: int, model_calls: int = 2):
    return PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=cash_cents,
            currency="PLN",
            model_calls=model_calls,
            reserve_fraction=0,
            max_concentration=1,
        )
    )


def _envelope(engine: HelisEngine, *, cash_cents: int = 5_000):
    return ResourceEnvelopeManager(engine).activate(_plan(engine, cash_cents=cash_cents))[0]


def test_reservation_reduces_available_cash_without_recording_cost(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    envelope = _envelope(engine)
    cash = CashReservationManager(engine)

    reservation = cash.reserve(
        envelope.id,
        amount_cents=3_000,
        source="validation-gateway",
        idempotency_key="validation-1",
    )

    assert reservation.status == CashReservationStatus.RESERVED
    assert cash.available_cash(envelope.id) == 2_000
    estimate = VentureValueEstimator(engine).estimate(opportunity.id, "PLN")
    assert estimate.observed_cost_cents == 0
    assert ResourceEnvelopeManager(engine).get(envelope.id).cash_consumed_cents == 0


def test_settle_records_only_actual_cost_and_releases_unused_reservation(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    envelope = _envelope(engine)
    cash = CashReservationManager(engine)
    reservation = cash.reserve(
        envelope.id,
        amount_cents=3_000,
        source="pricing-test",
        idempotency_key="pricing-1",
    )

    settled = cash.settle(reservation.id, actual_cents=1_800)

    assert settled.status == CashReservationStatus.SETTLED
    assert settled.settled_cents == 1_800
    assert cash.available_cash(envelope.id) == 3_200
    current = ResourceEnvelopeManager(engine).get(envelope.id)
    assert current is not None and current.cash_consumed_cents == 1_800
    estimate = VentureValueEstimator(engine).estimate(opportunity.id, "PLN")
    assert estimate.observed_cost_cents == 1_800

    again = cash.settle(reservation.id, actual_cents=1_800)
    assert again.id == settled.id
    assert VentureValueEstimator(engine).estimate(opportunity.id, "PLN").observed_cost_cents == 1_800
    with pytest.raises(EnvelopeConflict):
        cash.settle(reservation.id, actual_cents=1_801)


def test_release_restores_capacity_without_cost(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    envelope = _envelope(engine)
    cash = CashReservationManager(engine)
    reservation = cash.reserve(
        envelope.id,
        amount_cents=3_000,
        source="cancelled-test",
        idempotency_key="cancelled-1",
    )

    released = cash.release(reservation.id, reason="experiment cancelled before purchase")

    assert released.status == CashReservationStatus.RELEASED
    assert cash.available_cash(envelope.id) == 5_000
    assert VentureValueEstimator(engine).estimate(opportunity.id, "PLN").observed_cost_cents == 0
    assert cash.release(reservation.id, reason="duplicate callback").id == released.id


def test_reservation_is_idempotent_and_cannot_exceed_available_cash(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    envelope = _envelope(engine)
    cash = CashReservationManager(engine)

    first = cash.reserve(
        envelope.id,
        amount_cents=3_000,
        source="gateway",
        idempotency_key="commit-1",
    )
    same = cash.reserve(
        envelope.id,
        amount_cents=3_000,
        source="gateway",
        idempotency_key="commit-1",
    )
    assert same.id == first.id

    with pytest.raises(EnvelopeConflict):
        cash.reserve(
            envelope.id,
            amount_cents=3_001,
            source="gateway",
            idempotency_key="commit-1",
        )
    with pytest.raises(EnvelopeExceeded):
        cash.reserve(
            envelope.id,
            amount_cents=2_001,
            source="gateway",
            idempotency_key="commit-2",
        )


def test_open_reservation_blocks_new_plan_activation_until_resolved(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    envelopes = ResourceEnvelopeManager(engine)
    first_plan = _plan(engine, cash_cents=5_000)
    first_envelope = envelopes.activate(first_plan)[0]
    cash = CashReservationManager(engine)
    reservation = cash.reserve(
        first_envelope.id,
        amount_cents=2_000,
        source="external-test",
        idempotency_key="open-commitment",
    )

    second_plan = _plan(engine, cash_cents=6_000)
    with pytest.raises(sqlite3.IntegrityError, match="open cash reservation"):
        envelopes.activate(second_plan)

    cash.release(reservation.id, reason="cancel before reallocating portfolio")
    second_envelope = envelopes.activate(second_plan)[0]
    assert second_envelope.plan_id == second_plan.id


def test_runtime_cash_cap_uses_available_cash_after_reservations(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    envelope = _envelope(engine, cash_cents=500)
    cash = CashReservationManager(engine)
    cash.reserve(
        envelope.id,
        amount_cents=400,
        source="other-worker",
        idempotency_key="parallel-commitment",
    )
    runtime = VentureRuntime(engine, NoopProvider(), envelope.id)

    with pytest.raises(EnvelopeExceeded, match="open reservations"):
        runtime.validate(validation_cash_cents=101)


def test_direct_cash_consumption_cannot_overspend_an_open_reservation(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    envelope = _envelope(engine)
    cash = CashReservationManager(engine)
    cash.reserve(
        envelope.id,
        amount_cents=4_000,
        source="reserved-work",
        idempotency_key="reservation-1",
    )

    with pytest.raises(sqlite3.IntegrityError, match="overspent"):
        ResourceEnvelopeManager(engine).consume(
            envelope.id,
            source="other-cash-use",
            idempotency_key="consume-1",
            cash_cents=2_000,
        )
