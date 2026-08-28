from __future__ import annotations

from dataclasses import dataclass

import pytest

from helis.budget import CycleBudget
from helis.contact_gateway import ContactGatewayAck
from helis.domain import Opportunity, VentureStage
from helis.engine import HelisEngine
from helis.gtm_discovery import GTMDiscoveryMachine
from helis.gtm_domain import (
    Lead,
    LeadChannel,
    LeadStage,
    OutreachDraft,
    OutreachRunStatus,
    ProspectEvidence,
)
from helis.gtm_outreach import OutreachError, OutreachManager
from helis.gtm_store import GTMStore
from helis.store import HelisStore


class NoopProvider:
    def complete(self, *, system: str, user: str):
        raise AssertionError("model must not be called by lifecycle target checks")


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


def _opportunity(engine: HelisEngine, stage: VentureStage) -> Opportunity:
    opportunity = Opportunity(
        title=f"GTM lifecycle {stage.value}",
        problem="Service teams repeatedly lose time preparing manual quotes for customers.",
        customer="small service teams",
        proposed_value="prepare clearer quotes faster",
        stage=stage,
    )
    engine.ingest(opportunity)
    return opportunity


@pytest.mark.parametrize(
    "stage",
    [
        VentureStage.READY_PREVIEW,
        VentureStage.LAUNCHED,
        VentureStage.MEASURING,
        VentureStage.SCALING,
    ],
)
def test_discovery_remains_targetable_across_active_gtm_stages(tmp_path, stage) -> None:
    engine = HelisEngine(HelisStore(tmp_path / f"{stage.value}.db"))
    opportunity = _opportunity(engine, stage)

    report = GTMDiscoveryMachine(
        engine,
        NoopProvider(),
        CycleBudget(max_model_calls=1),
        gateway=None,
    ).tick(opportunity.id)

    assert report.opportunity_id == opportunity.id
    assert report.gateway_missing is True


@pytest.mark.parametrize("stage", [VentureStage.PAUSED, VentureStage.KILLED])
def test_discovery_refuses_stopped_gtm_stages(tmp_path, stage) -> None:
    engine = HelisEngine(HelisStore(tmp_path / f"{stage.value}.db"))
    opportunity = _opportunity(engine, stage)

    report = GTMDiscoveryMachine(
        engine,
        NoopProvider(),
        CycleBudget(max_model_calls=1),
        gateway=None,
    ).tick(opportunity.id)

    assert report.opportunity_id is None


def test_approved_outreach_is_rechecked_if_venture_pauses_before_dispatch(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _opportunity(engine, VentureStage.READY_PREVIEW)
    evidence = ProspectEvidence(
        source="public page",
        source_url="https://paused.example/services",
        reason="The public page describes individually prepared quotes.",
    )
    lead = Lead(
        opportunity_id=opportunity.id,
        organization="Paused Company",
        website="https://paused.example",
        contact_endpoint="https://paused.example/contact",
        channel=LeadChannel.WEBFORM,
        evidence=[evidence],
        fit_score=8,
        stage=LeadStage.DRAFTED,
    )
    state = GTMStore(engine.store)
    assert state.save_lead(lead)
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=lead.channel,
        body="Evidence-bound first contact with a respectful opt-out path.",
        evidence_ids=[evidence.id],
    )
    state.save_draft(draft)
    gateway = FakeContactGateway()
    manager = OutreachManager(engine, gateway=gateway)
    run = manager.prepare(draft.id)
    manager.approve(run.id)
    engine.store.save_opportunity(opportunity.model_copy(update={"stage": VentureStage.PAUSED}))

    with pytest.raises(OutreachError, match="not GTM-active"):
        manager.dispatch(run.id)

    assert gateway.calls == 0
    blocked = state.get_outreach_run(run.id)
    assert blocked is not None and blocked.status == OutreachRunStatus.BLOCKED
