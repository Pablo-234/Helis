from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from helis.contact_gateway import ContactGatewayAck
from helis.domain import Opportunity, VentureStage
from helis.engine import HelisEngine
from helis.gtm_domain import (
    Lead,
    LeadChannel,
    LeadResponse,
    LeadResponseKind,
    LeadStage,
    OutreachDraft,
    OutreachRunStatus,
    ProspectEvidence,
)
from helis.gtm_outreach import GTMContactPolicy, OutreachError, OutreachManager
from helis.gtm_store import GTMStore, lead_identity
from helis.store import HelisStore


@dataclass(slots=True)
class FakeContactGateway:
    name: str = "fake_contact_gateway"
    safe_destination: str = "https://send.example.test/outreach"
    calls: int = 0

    def send(self, run, lead, draft) -> ContactGatewayAck:
        self.calls += 1
        return ContactGatewayAck(
            accepted=True,
            dispatch_id=f"dispatch-{run.id}",
            channel=lead.channel.value,
        )


def _drafted_lead(engine: HelisEngine, *, domain: str = "acme.example") -> tuple[Lead, OutreachDraft]:
    opportunity = Opportunity(
        title="Bounded GTM",
        problem="Service teams repeatedly lose time preparing manual quotes for customers.",
        customer="small service teams",
        proposed_value="prepare clearer quotes faster",
        stage=VentureStage.READY_PREVIEW,
    )
    engine.store.save_opportunity(opportunity)
    evidence = ProspectEvidence(
        source="public service page",
        source_url=f"https://{domain}/services",
        reason="The public service page says customer projects receive individually prepared quotes.",
    )
    lead = Lead(
        opportunity_id=opportunity.id,
        organization=f"Company {domain}",
        website=f"https://{domain}",
        contact_endpoint=f"https://{domain}/contact",
        channel=LeadChannel.WEBFORM,
        evidence=[evidence],
        fit_score=8.0,
        stage=LeadStage.DRAFTED,
    )
    state = GTMStore(engine.store)
    assert state.save_lead(lead)
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=lead.channel,
        subject="Question about your quote workflow",
        body=(
            "Your public service page mentions individually prepared quotes. "
            "We are testing a simpler intake workflow. Would a short preview be useful? "
            "If not, no problem."
        ),
        evidence_ids=[evidence.id],
    )
    state.save_draft(draft)
    return lead, draft


def test_outreach_requires_approval_and_dispatch_is_idempotent(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    lead, draft = _drafted_lead(engine)
    gateway = FakeContactGateway()
    manager = OutreachManager(engine, gateway=gateway)

    run = manager.prepare(draft.id)
    assert run.status == OutreachRunStatus.WAITING_APPROVAL
    assert gateway.calls == 0
    manager.approve(run.id)
    sent = manager.dispatch(run.id, now=datetime(2026, 8, 28, 10, tzinfo=UTC))
    assert sent.status == OutreachRunStatus.WAITING_RESULT
    assert gateway.calls == 1
    assert GTMStore(engine.store).get_lead(lead.id).stage == LeadStage.CONTACTED

    repeated = manager.dispatch(run.id, now=datetime(2026, 8, 28, 11, tzinfo=UTC))
    assert repeated.external_ref == sent.external_ref
    assert gateway.calls == 1


def test_draft_mutation_after_approval_blocks_before_gateway(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _, draft = _drafted_lead(engine)
    gateway = FakeContactGateway()
    manager = OutreachManager(engine, gateway=gateway)
    run = manager.prepare(draft.id)
    manager.approve(run.id)

    changed = draft.model_copy(update={"body": draft.body + " Changed after approval."})
    GTMStore(engine.store).save_draft(changed)
    with pytest.raises(OutreachError, match="draft changed after approval"):
        manager.dispatch(run.id)
    assert gateway.calls == 0
    assert manager.state.get_outreach_run(run.id).status == OutreachRunStatus.BLOCKED


def test_daily_and_identity_caps_block_extra_contact(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    gateway = FakeContactGateway()
    policy = GTMContactPolicy(max_contacts_per_day=2, max_contacts_per_identity=1)
    manager = OutreachManager(engine, gateway=gateway, contact_policy=policy)
    now = datetime(2026, 8, 28, 10, tzinfo=UTC)

    _, first_draft = _drafted_lead(engine, domain="one.example")
    _, second_draft = _drafted_lead(engine, domain="two.example")
    _, third_draft = _drafted_lead(engine, domain="three.example")
    for draft in (first_draft, second_draft):
        run = manager.prepare(draft.id)
        manager.approve(run.id)
        manager.dispatch(run.id, now=now)
    third_run = manager.prepare(third_draft.id)
    manager.approve(third_run.id)
    with pytest.raises(OutreachError, match="daily contact cap reached"):
        manager.dispatch(third_run.id, now=now)
    assert gateway.calls == 2

    other_engine = HelisEngine(HelisStore(tmp_path / "identity.db"))
    other_gateway = FakeContactGateway()
    other_manager = OutreachManager(
        other_engine,
        gateway=other_gateway,
        contact_policy=policy,
    )
    lead, draft = _drafted_lead(other_engine, domain="same.example")
    run = other_manager.prepare(draft.id)
    other_manager.approve(run.id)
    other_manager.dispatch(run.id, now=now)

    state = GTMStore(other_engine.store)
    evidence = lead.evidence[0]
    second_lead = Lead(
        opportunity_id=lead.opportunity_id,
        organization="Same company alternate contact",
        website=lead.website,
        contact_endpoint="https://same.example/contact-2",
        channel=LeadChannel.WEBFORM,
        evidence=[evidence],
        fit_score=8,
        stage=LeadStage.DRAFTED,
    )
    # Store-level dedup correctly prevents a second lead for the same identity.
    assert state.save_lead(second_lead) is False


def test_not_interested_suppresses_and_sale_records_revenue_once(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    gateway = FakeContactGateway()
    manager = OutreachManager(engine, gateway=gateway)
    lead, draft = _drafted_lead(engine, domain="no.example")
    run = manager.prepare(draft.id)
    manager.approve(run.id)
    manager.dispatch(run.id)
    response = LeadResponse(
        run_id=run.id,
        lead_id=lead.id,
        opportunity_id=lead.opportunity_id,
        kind=LeadResponseKind.NOT_INTERESTED,
        summary="Asked not to be contacted again.",
    )
    manager.record_response(response)
    state = GTMStore(engine.store)
    assert state.is_suppressed(lead_identity(lead)) is True
    assert state.get_lead(lead.id).stage == LeadStage.SUPPRESSED

    sale_engine = HelisEngine(HelisStore(tmp_path / "sale.db"))
    sale_gateway = FakeContactGateway()
    sale_manager = OutreachManager(sale_engine, gateway=sale_gateway)
    sale_lead, sale_draft = _drafted_lead(sale_engine, domain="sale.example")
    sale_run = sale_manager.prepare(sale_draft.id)
    sale_manager.approve(sale_run.id)
    sale_manager.dispatch(sale_run.id)
    sale = LeadResponse(
        run_id=sale_run.id,
        lead_id=sale_lead.id,
        opportunity_id=sale_lead.opportunity_id,
        kind=LeadResponseKind.SALE,
        summary="Accepted the paid pilot.",
        revenue_cents=49_900,
        currency="PLN",
    )
    first_response, first_revenue = sale_manager.record_response(sale)
    second_response, second_revenue = sale_manager.record_response(sale)
    assert first_response.id == second_response.id
    assert first_revenue is not None
    assert second_revenue is not None
    assert first_revenue.id == second_revenue.id
    assert len(GTMStore(sale_engine.store).list_revenue(sale_lead.opportunity_id)) == 1
    assert GTMStore(sale_engine.store).get_lead(sale_lead.id).stage == LeadStage.WON
