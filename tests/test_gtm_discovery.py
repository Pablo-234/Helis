from __future__ import annotations

import json
from dataclasses import dataclass

from helis.budget import CycleBudget
from helis.domain import Opportunity, VentureStage
from helis.engine import HelisEngine
from helis.gtm_discovery import GTMDiscoveryMachine
from helis.gtm_domain import LeadChannel, LeadStage, ProspectEvidence
from helis.gtm_store import GTMStore
from helis.model_provider import ModelResult
from helis.prospect_gateway import ProspectCandidate
from helis.store import HelisStore


class FakeProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(content=json.dumps(self.payloads.pop(0)), prompt_tokens=10, completion_tokens=5)


@dataclass(slots=True)
class FakeProspectGateway:
    candidates: list[ProspectCandidate]
    name: str = "fake_prospect_gateway"
    safe_destination: str = "https://search.example.test"
    calls: int = 0

    def search(self, query):
        self.calls += 1
        return self.candidates[: query.max_results]


def test_gtm_discovers_qualifies_drafts_and_deduplicates(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Quote workflow",
        problem="Small service teams repeatedly lose time preparing manual customer quotes.",
        customer="small service teams",
        proposed_value="prepare clearer quotes faster",
        stage=VentureStage.READY_PREVIEW,
    )
    engine.ingest(opportunity)
    evidence = ProspectEvidence(
        source="public-directory",
        source_url="https://directory.example/acme",
        reason="Public service page says every project receives an individually prepared quote.",
        confidence=0.9,
    )
    gateway = FakeProspectGateway(
        candidates=[
            ProspectCandidate(
                organization="Acme Services",
                website="https://acme.example",
                contact_endpoint="https://acme.example/contact",
                channel=LeadChannel.WEBFORM,
                evidence=[evidence],
            )
        ]
    )
    provider = FakeProvider(
        [
            {
                "queries": [
                    {
                        "opportunity_id": str(opportunity.id),
                        "query": "service businesses custom quote request",
                        "target_customer": "small service teams",
                        "must_have_signals": ["custom quotes"],
                        "disqualifiers": ["fixed-price only"],
                        "max_results": 5,
                    }
                ]
            },
            {
                "assessments": [
                    {
                        "lead_id": "PLACEHOLDER",
                        "fit_score": 8.4,
                        "rationale": ["The public page explicitly indicates custom quote work."],
                        "supporting_evidence_ids": [str(evidence.id)],
                    }
                ]
            },
            {
                "drafts": [
                    {
                        "lead_id": "PLACEHOLDER",
                        "subject": "Question about your quote workflow",
                        "body": (
                            "I noticed your public service page mentions individually prepared quotes. "
                            "We are testing a simpler intake workflow for service teams. "
                            "Would it be useful if I sent a short preview? If not, no problem."
                        ),
                        "evidence_ids": [str(evidence.id)],
                    }
                ]
            },
        ]
    )

    original_complete = provider.complete

    def complete_with_lead_id(*, system: str, user: str) -> ModelResult:
        if provider.calls == 1:
            lead = GTMStore(engine.store).list_leads(opportunity.id)[0]
            provider.payloads[0]["assessments"][0]["lead_id"] = str(lead.id)
            provider.payloads[1]["drafts"][0]["lead_id"] = str(lead.id)
        return original_complete(system=system, user=user)

    provider.complete = complete_with_lead_id
    machine = GTMDiscoveryMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=3),
        gateway,
    )
    first = machine.tick(opportunity.id)
    assert first.leads_added == 1
    assert first.leads_qualified == 1
    assert first.drafts_created == 1
    leads = GTMStore(engine.store).list_leads(opportunity.id)
    assert leads[0].stage == LeadStage.DRAFTED
    assert leads[0].fit_score == 8.4
    assert provider.calls == 3

    second = machine.tick(opportunity.id)
    assert second.leads_added == 0
    assert second.leads_qualified == 0
    assert second.drafts_created == 0
    assert provider.calls == 3
    assert gateway.calls == 2


def test_gtm_rejects_unbound_qualification_evidence(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Evidence test",
        problem="A recurring operational problem needs a prospect evidence test.",
        customer="businesses",
        proposed_value="reduce repeated admin",
        stage=VentureStage.READY_PREVIEW,
    )
    engine.ingest(opportunity)
    evidence = ProspectEvidence(source="directory", reason="A real public workflow signal exists.")
    gateway = FakeProspectGateway(
        candidates=[
            ProspectCandidate(
                organization="Bounded Co",
                website="https://bounded.example",
                evidence=[evidence],
            )
        ]
    )
    provider = FakeProvider(
        [
            {
                "queries": [
                    {
                        "opportunity_id": str(opportunity.id),
                        "query": "business recurring admin",
                        "target_customer": "businesses",
                        "must_have_signals": [],
                        "disqualifiers": [],
                        "max_results": 3,
                    }
                ]
            },
            {
                "assessments": [
                    {
                        "lead_id": "PLACEHOLDER",
                        "fit_score": 9,
                        "rationale": ["Invented evidence should not pass."],
                        "supporting_evidence_ids": ["00000000-0000-0000-0000-000000000001"],
                    }
                ]
            },
        ]
    )
    original_complete = provider.complete

    def complete_with_lead_id(*, system: str, user: str) -> ModelResult:
        if provider.calls == 1:
            lead = GTMStore(engine.store).list_leads(opportunity.id)[0]
            provider.payloads[0]["assessments"][0]["lead_id"] = str(lead.id)
        return original_complete(system=system, user=user)

    provider.complete = complete_with_lead_id
    report = GTMDiscoveryMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=2),
        gateway,
    ).tick(opportunity.id)
    assert report.leads_added == 1
    assert report.leads_qualified == 0
    lead = GTMStore(engine.store).list_leads(opportunity.id)[0]
    assert lead.stage == LeadStage.DISCOVERED
