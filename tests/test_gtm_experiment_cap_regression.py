from __future__ import annotations

import json
from dataclasses import dataclass

from helis.budget import CycleBudget
from helis.domain import Opportunity, VentureStage
from helis.engine import HelisEngine
from helis.gtm_discovery import GTMDiscoveryMachine
from helis.gtm_domain import (
    Lead,
    LeadChannel,
    LeadStage,
    OutreachDraft,
    ProspectEvidence,
    ProspectQuery,
)
from helis.gtm_experiment import GTMExperimentManager
from helis.gtm_experiment_domain import GTMExperiment, GTMExperimentArm, GTMExperimentKind
from helis.gtm_experiment_store import GTMExperimentStore
from helis.gtm_store import GTMStore
from helis.model_provider import ModelResult
from helis.store import HelisStore


@dataclass(slots=True)
class DraftProvider:
    lead_id: str
    evidence_id: str
    calls: int = 0
    last_user: str = ""

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        self.last_user = user
        return ModelResult(
            content=json.dumps(
                {
                    "drafts": [
                        {
                            "lead_id": self.lead_id,
                            "subject": "Bounded offer test",
                            "body": "Would this bounded offer variant be worth a short conversation?",
                            "evidence_ids": [self.evidence_id],
                        }
                    ]
                }
            )
        )


@dataclass(slots=True)
class EmptyProspectGateway:
    calls: int = 0
    name: str = "empty"
    safe_destination: str = "https://prospect.example.test/search"

    def search(self, query):
        self.calls += 1
        return []


def _lead(opportunity_id, index: int, *, stage: LeadStage) -> Lead:
    evidence = ProspectEvidence(
        source="https://example.test/public-signal",
        source_url="https://example.test/public-signal",
        reason="The organization publicly exposes a workflow relevant to the tested venture.",
    )
    return Lead(
        opportunity_id=opportunity_id,
        organization=f"Studio {index}",
        website=f"https://studio-{index}.example.test",
        contact_endpoint=f"https://studio-{index}.example.test/contact",
        channel=LeadChannel.WEBFORM,
        evidence=[evidence],
        fit_score=8,
        fit_rationale=["Public workflow matches the tested problem."],
        stage=stage,
    )


def _experiment(opportunity_id) -> GTMExperiment:
    return GTMExperiment(
        opportunity_id=opportunity_id,
        kind=GTMExperimentKind.PRICING,
        hypothesis="A smaller starter package may improve qualified response quality.",
        arms=[
            GTMExperimentArm(
                key="control",
                label="Full package",
                offer_summary="Full onboarding package offered for 1000 PLN.",
                price_cents=100_000,
                currency="PLN",
            ),
            GTMExperimentArm(
                key="variant",
                label="Starter package",
                offer_summary="Smaller starter package offered for 500 PLN.",
                price_cents=50_000,
                currency="PLN",
            ),
        ],
        max_assignments_per_arm=2,
    )


def test_partial_assignment_capacity_cannot_leak_unassigned_drafts(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Automated booking concierge",
        problem="Small service businesses lose time coordinating bookings and quoting manually.",
        customer="small service businesses",
        proposed_value="Automate booking coordination and first-line customer service.",
        stage=VentureStage.MEASURING,
    )
    engine.store.save_opportunity(opportunity)
    gtm = GTMStore(engine.store)
    experiment = _experiment(opportunity.id)
    GTMExperimentStore(engine.store).save(experiment)

    # Fill the control arm and leave exactly one remaining assignment in variant.
    for index, arm_key in enumerate(("control", "control", "variant"), start=1):
        seeded = _lead(opportunity.id, index, stage=LeadStage.DRAFTED)
        gtm.save_lead(seeded)
        gtm.save_draft(
            OutreachDraft(
                lead_id=seeded.id,
                opportunity_id=opportunity.id,
                channel=seeded.channel,
                subject="Existing experiment draft",
                body="Existing bounded experiment draft used only to consume assignment capacity.",
                evidence_ids=[seeded.evidence[0].id],
                experiment_id=experiment.id,
                experiment_arm_key=arm_key,
            )
        )

    candidates = [_lead(opportunity.id, index, stage=LeadStage.QUALIFIED) for index in range(10, 13)]
    for lead in candidates:
        gtm.save_lead(lead)
    gtm.save_query(
        ProspectQuery(
            opportunity_id=opportunity.id,
            query="public booking workflow",
            target_customer="small service businesses",
            max_results=1,
        )
    )

    manager = GTMExperimentManager(engine)
    expected = manager.assign_for_leads(opportunity.id, candidates)
    assert len(expected) == 1
    assigned_lead_id = next(iter(expected))
    assigned_lead = next(lead for lead in candidates if lead.id == assigned_lead_id)

    provider = DraftProvider(str(assigned_lead.id), str(assigned_lead.evidence[0].id))
    gateway = EmptyProspectGateway()
    report = GTMDiscoveryMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=1),
        gateway,
        draft_limit=3,
        experiment_manager=manager,
    ).tick(opportunity.id)

    assert report.experiment_assignments == 1
    assert report.drafts_created == 1
    assert provider.calls == 1
    assert gateway.calls == 1

    request = json.loads(provider.last_user)
    assert [item["id"] for item in request["leads"]] == [str(assigned_lead.id)]
    assert set(request["lead_arm_assignments"]) == {str(assigned_lead.id)}

    fresh_drafts = [
        draft
        for draft in gtm.list_drafts(opportunity.id)
        if draft.lead_id in {lead.id for lead in candidates}
    ]
    assert len(fresh_drafts) == 1
    assert fresh_drafts[0].lead_id == assigned_lead.id
    assert fresh_drafts[0].experiment_id == experiment.id
    assert fresh_drafts[0].experiment_arm_key == "variant"

    unassigned = [lead for lead in candidates if lead.id != assigned_lead.id]
    for lead in unassigned:
        stored = gtm.get_lead(lead.id)
        assert stored is not None
        assert stored.stage == LeadStage.QUALIFIED
        assert gtm.get_draft_for_lead(lead.id) is None
