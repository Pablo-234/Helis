from __future__ import annotations

import json
from dataclasses import dataclass

from helis.budget import CycleBudget
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
    OutreachRun,
    OutreachRunStatus,
    ProspectEvidence,
)
from helis.gtm_outreach import draft_hash
from helis.gtm_runtime import GTMRuntime
from helis.gtm_store import GTMStore
from helis.model_provider import ModelResult
from helis.store import HelisStore


class NeverProvider:
    def complete(self, *, system: str, user: str) -> ModelResult:
        raise AssertionError("model provider must not be called")


class QueryProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(
            content=json.dumps(
                {
                    "queries": [
                        {
                            "opportunity_id": self.opportunity_id,
                            "query": "service businesses manual quoting",
                            "target_customer": "small service teams",
                            "must_have_signals": ["custom quotes"],
                            "disqualifiers": [],
                            "max_results": 3,
                        }
                    ]
                }
            ),
            prompt_tokens=10,
            completion_tokens=5,
        )

    opportunity_id: str = ""


@dataclass(slots=True)
class FakeProspectGateway:
    name: str = "fake_prospect_gateway"
    safe_destination: str = "https://search.example.test"
    calls: int = 0

    def search(self, query):
        self.calls += 1
        return []


@dataclass(slots=True)
class FakeContactGateway:
    name: str = "fake_contact_gateway"
    safe_destination: str = "https://send.example.test"
    calls: int = 0

    def send(self, run, lead, draft) -> ContactGatewayAck:
        self.calls += 1
        return ContactGatewayAck(
            accepted=True,
            dispatch_id=f"dispatch-{run.id}",
            channel=lead.channel.value,
        )


@dataclass(slots=True)
class FakeContactResultGateway:
    response_kind: LeadResponseKind = LeadResponseKind.SALE
    revenue_cents: int = 12_500
    calls: int = 0

    name: str = "fake_contact_result_gateway"
    safe_destination: str = "https://results.example.test"

    def fetch(self, run: OutreachRun) -> LeadResponse | None:
        self.calls += 1
        return LeadResponse(
            run_id=run.id,
            lead_id=run.lead_id,
            opportunity_id=run.opportunity_id,
            kind=self.response_kind,
            summary="Observed outcome from the operator-owned sales system.",
            revenue_cents=self.revenue_cents,
            currency="PLN",
        )


def _venture(engine: HelisEngine, stage: VentureStage = VentureStage.MEASURING) -> Opportunity:
    opportunity = Opportunity(
        title="Scheduled GTM venture",
        problem="Service teams repeatedly lose time preparing manual quotes for customers.",
        customer="small service teams",
        proposed_value="prepare clearer quotes faster",
        stage=stage,
    )
    engine.store.save_opportunity(opportunity)
    return opportunity


def _draft(engine: HelisEngine, opportunity: Opportunity, *, suffix: str = "one"):
    evidence = ProspectEvidence(
        source="public service page",
        source_url=f"https://{suffix}.example/services",
        reason="The public page says customer projects receive individually prepared quotes.",
    )
    lead = Lead(
        opportunity_id=opportunity.id,
        organization=f"Company {suffix}",
        website=f"https://{suffix}.example",
        contact_endpoint=f"https://{suffix}.example/contact",
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
        subject="Question about your quote workflow",
        body="Your public page mentions custom quotes. Would a short workflow preview be useful?",
        evidence_ids=[evidence.id],
    )
    state.save_draft(draft)
    return lead, draft


def test_existing_draft_is_prepared_without_model_call_or_send(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    _, draft = _draft(engine, opportunity)

    report = GTMRuntime(
        engine,
        NeverProvider(),
        CycleBudget(max_model_calls=0),
    ).tick(opportunity.id)

    run = GTMStore(engine.store).get_latest_run_for_draft(draft.id)
    assert run is not None
    assert run.status == OutreachRunStatus.WAITING_APPROVAL
    assert run.approval_granted is False
    assert report.prepared_run_id == run.id
    assert report.dispatched_run_id is None
    assert report.did_work is True


def test_already_approved_run_dispatches_with_zero_model_capacity(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine, VentureStage.SCALING)
    lead, draft = _draft(engine, opportunity)
    state = GTMStore(engine.store)
    run = OutreachRun(
        draft_id=draft.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        draft_hash=draft_hash(draft),
        status=OutreachRunStatus.READY,
        approval_granted=True,
    )
    state.save_outreach_run(run)
    gateway = FakeContactGateway()

    report = GTMRuntime(
        engine,
        NeverProvider(),
        CycleBudget(max_model_calls=0),
        contact_gateway=gateway,
    ).tick(opportunity.id)

    saved = state.get_outreach_run(run.id)
    assert gateway.calls == 1
    assert saved is not None and saved.status == OutreachRunStatus.WAITING_RESULT
    assert report.dispatched_run_id == run.id
    assert report.waiting_result == 1


def test_waiting_result_without_result_gateway_is_a_zero_model_gate(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine, VentureStage.LAUNCHED)
    lead, draft = _draft(engine, opportunity)
    state = GTMStore(engine.store)
    run = OutreachRun(
        draft_id=draft.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        draft_hash=draft_hash(draft),
        status=OutreachRunStatus.WAITING_RESULT,
        approval_granted=True,
        external_ref="dispatch-existing",
    )
    state.save_outreach_run(run)

    report = GTMRuntime(
        engine,
        NeverProvider(),
        CycleBudget(max_model_calls=0),
    ).tick(opportunity.id)

    assert report.reason == "contact_result_gateway_missing"
    assert report.waiting_result == 1
    assert state.list_responses(opportunity.id) == []
    assert state.list_revenue(opportunity.id) == []


def test_observed_sale_is_ingested_and_attributed_with_zero_model_calls(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine, VentureStage.LAUNCHED)
    lead, draft = _draft(engine, opportunity)
    state = GTMStore(engine.store)
    run = OutreachRun(
        draft_id=draft.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        draft_hash=draft_hash(draft),
        status=OutreachRunStatus.WAITING_RESULT,
        approval_granted=True,
        external_ref="dispatch-sale-1",
    )
    state.save_outreach_run(run)
    gateway = FakeContactResultGateway(revenue_cents=12_500)

    report = GTMRuntime(
        engine,
        NeverProvider(),
        CycleBudget(max_model_calls=0),
        contact_result_gateway=gateway,
    ).tick(opportunity.id)

    saved_run = state.get_outreach_run(run.id)
    saved_lead = state.get_lead(lead.id)
    responses = state.list_responses(opportunity.id)
    revenue = state.list_revenue(opportunity.id)
    assert gateway.calls == 1
    assert report.reason == "observed_sale_ingested"
    assert report.did_work is True
    assert saved_run is not None and saved_run.status == OutreachRunStatus.COMPLETED
    assert saved_lead is not None and saved_lead.stage == LeadStage.WON
    assert len(responses) == 1 and responses[0].kind == LeadResponseKind.SALE
    assert len(revenue) == 1
    assert revenue[0].amount_cents == 12_500
    assert revenue[0].opportunity_id == opportunity.id
    assert revenue[0].lead_id == lead.id

    second = GTMRuntime(
        engine,
        NeverProvider(),
        CycleBudget(max_model_calls=0),
        contact_result_gateway=gateway,
    ).tick(opportunity.id)
    assert gateway.calls == 1
    assert len(state.list_revenue(opportunity.id)) == 1
    assert second.reason in {"prospect_gateway_missing", "no_model_capacity"}


def test_approval_backlog_prevents_more_discovery(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    state = GTMStore(engine.store)
    for index in range(3):
        lead, draft = _draft(engine, opportunity, suffix=f"backlog-{index}")
        state.save_outreach_run(
            OutreachRun(
                draft_id=draft.id,
                lead_id=lead.id,
                opportunity_id=opportunity.id,
                draft_hash=draft_hash(draft),
                status=OutreachRunStatus.WAITING_APPROVAL,
            )
        )
    gateway = FakeProspectGateway()

    report = GTMRuntime(
        engine,
        NeverProvider(),
        CycleBudget(max_model_calls=5),
        prospect_gateway=gateway,
    ).tick(opportunity.id)

    assert report.reason == "approval_backlog"
    assert report.waiting_approval == 3
    assert gateway.calls == 0
    assert report.did_work is False


def test_missing_prospect_gateway_does_not_spend_model_calls(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)

    report = GTMRuntime(
        engine,
        NeverProvider(),
        CycleBudget(max_model_calls=5),
    ).tick(opportunity.id)

    assert report.reason == "prospect_gateway_missing"
    assert report.did_work is False


def test_discovery_runs_only_when_local_backlog_is_empty(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    provider = QueryProvider()
    provider.opportunity_id = str(opportunity.id)
    gateway = FakeProspectGateway()
    budget = CycleBudget(max_model_calls=1)

    report = GTMRuntime(
        engine,
        provider,
        budget,
        prospect_gateway=gateway,
    ).tick(opportunity.id)

    assert provider.calls == 1
    assert budget.model_calls == 1
    assert gateway.calls == 1
    assert report.discovery is not None
    assert report.discovery.queries_planned == 1
    assert report.reason == "discovery_completed"
    assert report.did_work is True
