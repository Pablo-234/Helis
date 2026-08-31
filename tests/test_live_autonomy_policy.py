from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from helis.autopilot import AutopilotPolicy
from helis.budget import CycleBudget
from helis.commerce_gateway import CheckoutGatewayAck
from helis.commerce_manager import CommerceManager
from helis.contact_gateway import ContactGatewayAck
from helis.domain import (
    BusinessModelHypothesis,
    DeliveryModel,
    Opportunity,
    PreviewManifest,
    PricingHypothesis,
    RevenueModel,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.gtm_domain import (
    Lead,
    LeadChannel,
    LeadStage,
    OutreachDraft,
    OutreachRunStatus,
    ProspectEvidence,
)
from helis.gtm_runtime import GTMRuntime
from helis.gtm_store import GTMStore
from helis.model_provider import ModelResult
from helis.policy import ActionKind, ActionRequest, AutonomyPolicy
from helis.preview_domain import PreviewPublishStatus
from helis.preview_publisher import PreviewPublisher
from helis.store import HelisStore


class NeverProvider:
    def complete(self, *, system: str, user: str) -> ModelResult:
        raise AssertionError("model provider must not be called")


@dataclass(slots=True)
class FakeCommerceGateway:
    name: str = "fake_commerce"
    safe_destination: str = "https://payments.example.test"
    calls: int = 0

    def create_checkout(self, run, offer) -> CheckoutGatewayAck:
        self.calls += 1
        return CheckoutGatewayAck(
            accepted=True,
            external_ref=f"checkout-{run.id}",
            checkout_url=f"https://payments.example.test/pay/{run.id}",
        )

    def poll_payment(self, binding):
        return None


@dataclass(slots=True)
class FakeContactGateway:
    name: str = "fake_contact"
    safe_destination: str = "https://mail.example.test"
    calls: int = 0

    def send(self, run, lead, draft) -> ContactGatewayAck:
        self.calls += 1
        return ContactGatewayAck(
            accepted=True,
            dispatch_id=f"mail-{run.id}",
            channel=lead.channel.value,
        )


def _self_serve_venture(engine: HelisEngine) -> Opportunity:
    model = BusinessModelHypothesis(
        name="Small paid workflow",
        payer="small service teams",
        offer="A focused online workflow product that removes one repetitive quoting task.",
        value_proposition="Save time on a repeated quoting workflow without adopting a large suite.",
        revenue_model=RevenueModel.FIXED_FEE,
        delivery_model=DeliveryModel.SOFTWARE,
        pricing=PricingHypothesis(
            currency="PLN",
            low_cents=2500,
            high_cents=2500,
            unit="one purchase",
        ),
        acquisition_wedge="Reach teams already describing the manual workflow publicly.",
        fulfillment="Deliver the validated workflow as a bounded online product.",
        automation_roles=["generate structured workflow output"],
        human_roles=[],
        time_to_first_revenue_days=7,
        gross_margin_pct=90,
        owner_minutes_per_week_at_scale=30,
        test_cost_cents=0,
        primary_risks=["weak willingness to pay"],
    )
    opportunity = Opportunity(
        title="Self serve venture",
        problem="Small service teams repeatedly spend time on a manual quoting workflow.",
        customer="small service teams",
        proposed_value="Reduce repeated manual quoting work.",
        business_model=model,
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    return opportunity


def _gtm_draft(engine: HelisEngine) -> tuple[Opportunity, OutreachDraft]:
    opportunity = Opportunity(
        title="B2B venture",
        problem="Small teams repeatedly lose time preparing manual project quotes.",
        customer="small service teams",
        proposed_value="Prepare clearer quotes faster.",
        stage=VentureStage.MEASURING,
    )
    engine.store.save_opportunity(opportunity)
    evidence = ProspectEvidence(
        source="public page",
        source_url="https://example.test/contact",
        reason="The public page describes individually prepared customer quotes.",
    )
    lead = Lead(
        opportunity_id=opportunity.id,
        organization="Example Company",
        website="https://example.test",
        contact_endpoint="owner@example.test",
        channel=LeadChannel.EMAIL,
        evidence=[evidence],
        fit_score=8,
        stage=LeadStage.DRAFTED,
    )
    state = GTMStore(engine.store)
    assert state.save_lead(lead)
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=LeadChannel.EMAIL,
        contact_endpoint="owner@example.test",
        subject="Question about your quoting workflow",
        body="Your public page mentions custom quotes. Would a short workflow preview be useful?",
        evidence_ids=[evidence.id],
    )
    state.save_draft(draft)
    return opportunity, draft


def test_live_auto_grants_only_three_narrow_action_categories() -> None:
    policy = AutopilotPolicy(
        allow_checkout_without_approval=True,
        allow_publication_without_approval=True,
        allow_first_contact_without_approval=True,
        cash_cents=50_000,
    ).autonomy_policy()

    for kind in {
        ActionKind.CHECKOUT_CREATE,
        ActionKind.PUBLICATION,
        ActionKind.EXTERNAL_CONTACT,
    }:
        decision = policy.evaluate(ActionRequest(kind=kind, description="explicit live grant"))
        assert decision.allowed is True
        assert decision.requires_approval is False

    for kind in {
        ActionKind.NETWORK_WRITE,
        ActionKind.CREDENTIAL_ACCESS,
        ActionKind.SELF_MODIFY,
    }:
        decision = policy.evaluate(ActionRequest(kind=kind, description="not granted"))
        assert decision.allowed is False
        assert decision.requires_approval is True

    spend = policy.evaluate(
        ActionRequest(kind=ActionKind.SPEND, description="one cent spend", estimated_cost_cents=1)
    )
    assert spend.allowed is False
    assert policy.autonomous_spend_limit_cents == 0


def test_checkout_grant_activates_exact_checkout_without_manual_approval(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _self_serve_venture(engine)
    gateway = FakeCommerceGateway()
    manager = CommerceManager(
        engine,
        gateway=gateway,
        policy=AutonomyPolicy(allow_checkout_creation_without_approval=True),
    )

    report = manager.advance_prebuild(opportunity.id)

    assert gateway.calls == 1
    assert report.binding_created is True
    assert report.run is not None
    assert report.run.approval_granted is True
    assert report.run.status.value == "active"
    assert report.binding is not None
    assert report.binding.checkout_url.startswith("https://payments.example.test/pay/")


def test_publication_grant_prepares_hash_locked_run_as_ready(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Publishable venture",
        problem="Teams repeatedly lose time on a manual workflow that can be automated.",
        customer="small teams",
        proposed_value="Remove one repeated manual workflow.",
        stage=VentureStage.BUILDING,
    )
    engine.store.save_opportunity(opportunity)
    preview = PreviewManifest(
        run_id=uuid4(),
        opportunity_id=opportunity.id,
        workspace=str(tmp_path / "workspace"),
        entrypoint="index.html",
        artifact_hash="a" * 64,
    )
    engine.record_preview_manifest(preview)
    publisher = PreviewPublisher(
        engine,
        policy=AutonomyPolicy(allow_publication_without_approval=True),
    )

    run = publisher.prepare(opportunity.id)

    assert run is not None
    assert run.status == PreviewPublishStatus.READY
    assert run.approval_granted is True
    assert run.artifact_hash == preview.artifact_hash


def test_first_contact_grant_removes_approval_backlog_but_keeps_dispatch_bounded(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity, draft = _gtm_draft(engine)
    gateway = FakeContactGateway()
    runtime = GTMRuntime(
        engine,
        NeverProvider(),
        CycleBudget(max_model_calls=0),
        contact_gateway=gateway,
        autonomy_policy=AutonomyPolicy(allow_external_contact_without_approval=True),
    )

    prepared_report = runtime.tick(opportunity.id)
    state = GTMStore(engine.store)
    run = state.get_latest_run_for_draft(draft.id)

    assert run is not None
    assert run.status == OutreachRunStatus.READY
    assert run.approval_granted is True
    assert prepared_report.waiting_approval == 0
    assert gateway.calls == 0

    dispatched_report = runtime.tick(opportunity.id)
    saved = state.get_outreach_run(run.id)
    assert saved is not None
    assert saved.status == OutreachRunStatus.WAITING_RESULT
    assert gateway.calls == 1
    assert dispatched_report.dispatched_run_id == run.id
