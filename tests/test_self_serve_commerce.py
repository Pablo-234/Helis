from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from helis.budget import CycleBudget
from helis.builder_machine import BuilderMachine
from helis.commerce_build_verifier import CommerceBuildVerifier
from helis.commerce_domain import PaymentGatewayResult, PaymentResultStatus
from helis.commerce_gateway import CheckoutGatewayAck
from helis.commerce_manager import CommerceManager
from helis.domain import (
    BuildBundle,
    BuildFile,
    BuildSpec,
    BuildTemplate,
    BusinessModelHypothesis,
    DeliveryModel,
    Opportunity,
    PricingHypothesis,
    RevenueModel,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.portfolio_value import VentureValueEstimator
from helis.store import HelisStore


class NeverProvider:
    def complete(self, *, system: str, user: str) -> ModelResult:
        raise AssertionError("model provider must not be called")


class FakeProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(content=json.dumps(self.payloads.pop(0)))


@dataclass(slots=True)
class FakeCommerceGateway:
    name: str = "fake_commerce_gateway"
    safe_destination: str = "https://commerce.example.test/api"
    create_calls: int = 0
    poll_calls: int = 0
    payment: PaymentGatewayResult | None = None

    def create_checkout(self, run, offer) -> CheckoutGatewayAck:
        self.create_calls += 1
        assert run.offer_hash == offer.offer_hash
        return CheckoutGatewayAck(
            accepted=True,
            external_ref=f"checkout-{run.id}",
            checkout_url=f"https://pay.example.test/{run.id}",
        )

    def poll_payment(self, binding) -> PaymentGatewayResult | None:
        self.poll_calls += 1
        return self.payment


def _self_serve_venture(engine: HelisEngine) -> Opportunity:
    model = BusinessModelHypothesis(
        name="Signal Brief",
        payer="small B2B teams",
        offer="A concise recurring market signal brief delivered online.",
        value_proposition="Reduce manual research time while keeping source links visible.",
        revenue_model=RevenueModel.SUBSCRIPTION,
        delivery_model=DeliveryModel.DATA_PRODUCT,
        pricing=PricingHypothesis(
            currency="PLN",
            low_cents=4900,
            high_cents=9900,
            unit="per month",
        ),
        acquisition_wedge="Public examples showing useful signal density.",
        fulfillment="Generate and deliver a bounded digital brief from public evidence.",
        automation_roles=["collect public signals", "prepare brief"],
        human_roles=[],
        time_to_first_revenue_days=7,
        gross_margin_pct=90,
        owner_minutes_per_week_at_scale=20,
        test_cost_cents=0,
        primary_risks=["weak willingness to pay"],
    )
    opportunity = Opportunity(
        title="Self-serve signal brief",
        problem="Small B2B teams spend hours each week manually scanning scattered market sources.",
        customer="small B2B teams",
        proposed_value="deliver a concise evidence-linked signal brief automatically",
        tags=["online_venture"],
        business_model=model,
        business_model_score=82,
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    return opportunity


def test_checkout_is_deterministic_approval_gated_and_idempotent(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _self_serve_venture(engine)
    gateway = FakeCommerceGateway()
    manager = CommerceManager(engine, gateway=gateway)

    planned = manager.advance_prebuild(opportunity.id)
    assert planned.created is True
    assert planned.offer is not None and planned.offer.price_cents == 4900
    assert planned.offer.display_price == "49.00 PLN"
    assert planned.run is not None and planned.run.approval_granted is False
    assert planned.reason == "commerce_checkout_waiting_approval"
    assert gateway.create_calls == 0

    repeated = manager.advance_prebuild(opportunity.id)
    assert repeated.run is not None and planned.run is not None
    assert repeated.run.id == planned.run.id
    assert repeated.created is False
    assert gateway.create_calls == 0

    manager.approve(planned.run.id)
    activated = manager.advance_prebuild(opportunity.id)
    assert activated.binding_created is True
    assert activated.binding is not None
    assert activated.binding.checkout_url.startswith("https://pay.example.test/")
    assert gateway.create_calls == 1

    stable = manager.advance_prebuild(opportunity.id)
    assert stable.reason == "commerce_checkout_active"
    assert stable.binding is not None and stable.binding.id == activated.binding.id
    assert gateway.create_calls == 1


def test_builder_refuses_self_serve_venture_before_checkout(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _self_serve_venture(engine)

    report = BuilderMachine(
        engine,
        NeverProvider(),
        CycleBudget(max_model_calls=0),
        workspace_root=tmp_path / "workspaces",
    ).tick(opportunity.id)

    assert report.blocked_reason == "self-serve commerce checkout must be active before build"


def test_builder_hash_locks_exact_checkout_and_price(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _self_serve_venture(engine)
    gateway = FakeCommerceGateway()
    manager = CommerceManager(engine, gateway=gateway)
    planned = manager.advance_prebuild(opportunity.id)
    assert planned.run is not None
    manager.approve(planned.run.id)
    activated = manager.advance_prebuild(opportunity.id)
    assert activated.binding is not None and activated.offer is not None

    checkout_url = activated.binding.checkout_url
    provider = FakeProvider(
        [
            {
                "template": "concierge_ops_v1",
                "name": "Signal Brief",
                "goal": "Present the validated signal brief and its exact approved purchase path.",
                "acceptance_criteria": [
                    "Explain the evidence-linked brief",
                    "State who the offer is for",
                ],
            },
            {
                "files": [
                    {
                        "path": "index.html",
                        "content": (
                            "<!doctype html><html><body><h1>Signal Brief</h1>"
                            "<p>Evidence-linked market signals for small B2B teams.</p>"
                            "<p>49.00 PLN</p>"
                            f'<a href="{checkout_url}">Buy Signal Brief</a>'
                            "</body></html>"
                        ),
                    },
                    {
                        "path": "README.md",
                        "content": (
                            "# Signal Brief\n\nBounded public preview using the exact approved checkout."
                        ),
                    },
                ]
            },
            {
                "verdict": "pass",
                "score": 9,
                "blocking_issues": [],
                "warnings": [],
                "summary": "The offer matches the validated scope and approved checkout contract.",
            },
        ]
    )

    report = BuilderMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=3),
        workspace_root=tmp_path / "workspaces",
    ).tick(opportunity.id)

    assert report.spec is not None and report.spec.template == BuildTemplate.STATIC_WEB
    assert report.preview is not None
    assert report.checks is not None
    assert all(check.passed for check in report.checks)
    assert provider.calls == 3
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.READY_PREVIEW


def test_commerce_verifier_rejects_alternate_destination_and_wrong_price() -> None:
    from helis.commerce_domain import BillingMode, CommerceBuildContext

    context = CommerceBuildContext(
        offer_id="00000000-0000-0000-0000-000000000001",
        offer_hash="a" * 64,
        checkout_url="https://pay.example.test/exact",
        price_cents=4900,
        currency="PLN",
        display_price="49.00 PLN",
        billing_mode=BillingMode.SUBSCRIPTION,
    )
    spec = BuildSpec(
        opportunity_id="00000000-0000-0000-0000-000000000002",
        template=BuildTemplate.STATIC_WEB,
        name="Checkout test",
        goal="Verify that the exact approved checkout contract cannot be broadened.",
        acceptance_criteria=["Exact price", "Exact checkout"],
    )
    bundle = BuildBundle(
        files=[
            BuildFile(
                path="index.html",
                content=(
                    "<html><body><p>99.00 PLN</p>"
                    '<a href="https://evil.example.test/pay">Buy</a></body></html>'
                ),
            ),
            BuildFile(path="README.md", content="# Test\n\nVerifier fixture."),
        ]
    )

    checks = CommerceBuildVerifier().verify(spec, bundle, context)
    assert {check.name for check in checks if not check.passed} == {
        "commerce_checkout_exact",
        "commerce_no_alternate_http_destinations",
        "commerce_price_exact",
    }


def test_observed_payment_becomes_direct_revenue_without_model(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _self_serve_venture(engine)
    gateway = FakeCommerceGateway()
    manager = CommerceManager(engine, gateway=gateway)
    planned = manager.advance_prebuild(opportunity.id)
    assert planned.run is not None
    manager.approve(planned.run.id)
    manager.advance_prebuild(opportunity.id)

    gateway.payment = PaymentGatewayResult(
        status=PaymentResultStatus.PAID,
        external_ref="payment-001",
        amount_cents=4900,
        currency="PLN",
    )
    observed = manager.poll_payment(opportunity.id)
    assert observed.revenue_created is True
    assert observed.revenue is not None and observed.revenue.amount_cents == 4900
    assert VentureValueEstimator(engine).estimate(opportunity.id, "PLN").observed_revenue_cents == 4900

    repeated = manager.poll_payment(opportunity.id)
    assert repeated.revenue_created is False
    assert len(manager.state.list_revenue(opportunity.id)) == 1


def test_pending_payment_cannot_claim_money() -> None:
    with pytest.raises(ValidationError):
        PaymentGatewayResult(
            status=PaymentResultStatus.PENDING,
            external_ref="fake-payment",
            amount_cents=4900,
            currency="PLN",
        )
