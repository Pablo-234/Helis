from __future__ import annotations

from uuid import uuid4

from helis.domain import (
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.gtm_domain import LeadResponse, LeadResponseKind, RevenueEvent
from helis.gtm_store import GTMStore
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.portfolio_value import VentureCostEvent, VentureValueEstimator
from helis.store import HelisStore


def _venture(engine: HelisEngine, title: str = "Economics venture") -> Opportunity:
    opportunity = Opportunity(
        title=title,
        problem="A recurring manual workflow creates measurable cost and delay for service businesses.",
        customer="small service teams",
        proposed_value="reduce recurring workflow cost",
        stage=VentureStage.MEASURING,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(
                capital_efficiency=8,
                execution_risk=3,
                evidence_strength=7,
            ),
            total=72,
            recommendation=Recommendation.VALIDATE,
            rationale=["test fixture"],
        )
    )
    return opportunity


def _response(
    state: GTMStore,
    opportunity: Opportunity,
    kind: LeadResponseKind,
    *,
    revenue_cents: int = 0,
    currency: str = "PLN",
) -> LeadResponse:
    response = LeadResponse(
        run_id=uuid4(),
        lead_id=uuid4(),
        opportunity_id=opportunity.id,
        kind=kind,
        summary=f"Resolved outcome: {kind.value}",
        revenue_cents=revenue_cents,
        currency=currency,
    )
    state.save_response(response)
    if revenue_cents:
        state.save_revenue(
            RevenueEvent(
                opportunity_id=opportunity.id,
                lead_id=response.lead_id,
                response_id=response.id,
                amount_cents=revenue_cents,
                currency=currency,
                source="test",
            )
        )
    return response


def test_empty_economics_exposes_prior_but_zero_confidence(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)

    estimate = VentureValueEstimator(engine).estimate(opportunity.id, "pln")

    assert estimate.currency == "PLN"
    assert estimate.resolved_outcomes == 0
    assert estimate.posterior_paid_sale_probability == 0.1
    assert estimate.expected_revenue_per_next_resolved_contact_cents == 0
    assert estimate.evidence_confidence == 0
    assert estimate.uncertainty == 1


def test_expected_value_uses_only_matching_currency_and_real_costs(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    state = GTMStore(engine.store)
    for _ in range(2):
        _response(state, opportunity, LeadResponseKind.SALE, revenue_cents=5_000, currency="PLN")
    _response(state, opportunity, LeadResponseKind.SALE, revenue_cents=20_000, currency="EUR")
    for _ in range(7):
        _response(state, opportunity, LeadResponseKind.NO_RESPONSE)

    estimator = VentureValueEstimator(engine)
    estimator.record_cost(
        VentureCostEvent(
            opportunity_id=opportunity.id,
            amount_cents=3_000,
            currency="PLN",
            source="outreach-test",
            external_ref="cost-1",
        )
    )
    estimator.record_cost(
        VentureCostEvent(
            opportunity_id=opportunity.id,
            amount_cents=9_000,
            currency="EUR",
            source="outreach-test",
            external_ref="cost-2",
        )
    )

    pln = estimator.estimate(opportunity.id, "PLN")
    eur = estimator.estimate(opportunity.id, "EUR")

    assert pln.resolved_outcomes == 10
    assert pln.paid_sales == 2
    assert pln.observed_revenue_cents == 10_000
    assert pln.observed_cost_cents == 3_000
    assert pln.realized_net_cents == 7_000
    assert pln.average_paid_sale_value_cents == 5_000
    assert pln.expected_revenue_per_next_resolved_contact_cents == 750
    assert pln.expected_net_per_next_resolved_contact_cents == 450
    assert pln.realized_roi is not None and pln.realized_roi > 2

    assert eur.paid_sales == 1
    assert eur.observed_revenue_cents == 20_000
    assert eur.observed_cost_cents == 9_000


def test_cost_external_reference_is_idempotent(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    estimator = VentureValueEstimator(engine)

    first = estimator.record_cost(
        VentureCostEvent(
            opportunity_id=opportunity.id,
            amount_cents=1_500,
            currency="PLN",
            source="gateway",
            external_ref="invoice-123",
        )
    )
    second = estimator.record_cost(
        VentureCostEvent(
            opportunity_id=opportunity.id,
            amount_cents=1_500,
            currency="PLN",
            source="gateway",
            external_ref="invoice-123",
        )
    )

    assert first.id == second.id
    assert len(estimator.economics.list_costs(opportunity.id)) == 1


def test_new_cost_changes_portfolio_snapshot_and_reduces_priority_with_evidence(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    state = GTMStore(engine.store)
    for _ in range(8):
        _response(state, opportunity, LeadResponseKind.NO_RESPONSE)

    budget = PortfolioBudget(cash_cents=50_000, currency="PLN", model_calls=20)
    allocator = PortfolioAllocator(engine)
    before = allocator.plan(budget)
    before_candidate = next(item for item in before.candidates if item.opportunity_id == opportunity.id)

    VentureValueEstimator(engine).record_cost(
        VentureCostEvent(
            opportunity_id=opportunity.id,
            amount_cents=8_000,
            currency="PLN",
            source="experiment",
            external_ref="experiment-1",
        )
    )
    after = allocator.plan(budget)
    after_candidate = next(item for item in after.candidates if item.opportunity_id == opportunity.id)

    assert after.id != before.id
    assert after.snapshot_hash != before.snapshot_hash
    assert after_candidate.value_estimate.expected_net_per_next_resolved_contact_cents < 0
    assert after_candidate.priority_score < before_candidate.priority_score
