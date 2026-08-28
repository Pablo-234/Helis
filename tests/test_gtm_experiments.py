from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pytest

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
from helis.gtm_experiment import GTMExperimentManager
from helis.gtm_experiment_domain import (
    GTMExperiment,
    GTMExperimentArm,
    GTMExperimentKind,
    GTMExperimentStatus,
)
from helis.gtm_experiment_store import GTMExperimentStore
from helis.gtm_outreach import OutreachError, OutreachManager, draft_hash
from helis.gtm_runtime import GTMRuntime
from helis.gtm_store import GTMStore
from helis.model_provider import ModelResult
from helis.outreach_drafter import OutreachDrafter
from helis.store import HelisStore


@dataclass(slots=True)
class FakeProvider:
    payloads: list[dict]
    calls: int = 0
    last_user: str = ""

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        self.last_user = user
        return ModelResult(
            content=json.dumps(self.payloads.pop(0)),
            prompt_tokens=10,
            completion_tokens=10,
        )


@dataclass(slots=True)
class FakeContactGateway:
    calls: int = 0
    name: str = "fake_contact"
    safe_destination: str = "https://contact.example.test/send"

    def send(self, run, lead, draft) -> ContactGatewayAck:
        self.calls += 1
        return ContactGatewayAck(
            accepted=True,
            dispatch_id=f"dispatch-{run.id}",
            channel=draft.channel.value,
        )


@dataclass(slots=True)
class NeverSearchGateway:
    calls: int = 0
    name: str = "never_search"
    safe_destination: str = "https://prospect.example.test/search"

    def search(self, query):
        self.calls += 1
        raise AssertionError("prospect search was not expected")


def _engine(tmp_path) -> HelisEngine:
    return HelisEngine(HelisStore(tmp_path / "helis.db"))


def _opportunity() -> Opportunity:
    return Opportunity(
        title="Automated booking concierge",
        problem="Small service businesses lose time coordinating bookings and quoting manually.",
        customer="small service businesses",
        proposed_value="Automate booking coordination and first-line customer service.",
        stage=VentureStage.MEASURING,
    )


def _evidence() -> ProspectEvidence:
    return ProspectEvidence(
        source="https://example.test/public-signal",
        source_url="https://example.test/public-signal",
        reason="The organization publicly exposes a booking workflow relevant to this venture.",
    )


def _lead(opportunity_id, index: int, *, stage: LeadStage = LeadStage.QUALIFIED) -> Lead:
    return Lead(
        opportunity_id=opportunity_id,
        organization=f"Studio {index}",
        website=f"https://studio-{index}.example.test",
        contact_endpoint=f"https://studio-{index}.example.test/contact",
        channel=LeadChannel.WEBFORM,
        evidence=[_evidence()],
        fit_score=8,
        fit_rationale=["Public booking workflow matches the tested problem."],
        stage=stage,
    )


def _experiment(opportunity_id, *, max_assignments: int = 5) -> GTMExperiment:
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
        minimum_resolved_per_arm=2,
        max_resolved_per_arm=2,
        max_assignments_per_arm=max_assignments,
        minimum_lift=0.20,
    )


def _plan_payload() -> dict:
    return {
        "kind": "pricing",
        "hypothesis": "A smaller starter package may improve qualified response quality.",
        "arms": [
            {
                "key": "control",
                "label": "Full package",
                "offer_summary": "Full onboarding package offered for 1000 PLN.",
                "price_cents": 100000,
                "currency": "PLN",
            },
            {
                "key": "variant",
                "label": "Starter package",
                "offer_summary": "Smaller starter package offered for 500 PLN.",
                "price_cents": 50000,
                "currency": "PLN",
            },
        ],
    }


def test_no_response_means_no_experiment_and_zero_model_calls(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    provider = FakeProvider([])
    manager = GTMExperimentManager(
        engine,
        provider,
        CycleBudget(max_model_calls=1),
    )

    result = manager.plan_if_eligible(opportunity.id)

    assert result.experiment is None
    assert result.created is False
    assert provider.calls == 0


def test_first_response_plans_exactly_one_experiment(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    gtm = GTMStore(engine.store)
    lead = _lead(opportunity.id, 1)
    gtm.save_lead(lead)
    gtm.save_response(
        LeadResponse(
            run_id=__import__("uuid").uuid4(),
            lead_id=lead.id,
            opportunity_id=opportunity.id,
            kind=LeadResponseKind.INTERESTED,
            summary="Interested in learning more.",
        )
    )
    provider = FakeProvider([_plan_payload()])
    manager = GTMExperimentManager(
        engine,
        provider,
        CycleBudget(max_model_calls=2),
    )

    first = manager.plan_if_eligible(opportunity.id)
    second = manager.plan_if_eligible(opportunity.id)

    assert first.created is True
    assert first.experiment is not None
    assert first.experiment.kind == GTMExperimentKind.PRICING
    assert second.experiment is not None
    assert second.experiment.id == first.experiment.id
    assert second.created is False
    assert provider.calls == 1


def test_pricing_experiment_rejects_missing_price_or_more_than_four_x_spread() -> None:
    base = {
        "opportunity_id": __import__("uuid").uuid4(),
        "kind": GTMExperimentKind.PRICING,
        "hypothesis": "Test a bounded explicit price difference between two comparable offers.",
    }
    with pytest.raises(ValueError, match="explicit price"):
        GTMExperiment(
            **base,
            arms=[
                GTMExperimentArm(
                    key="control",
                    label="Control",
                    offer_summary="Control package offered at a clear reference price.",
                    price_cents=10_000,
                ),
                GTMExperimentArm(
                    key="variant",
                    label="Variant",
                    offer_summary="Variant package tests a different explicit price point.",
                    price_cents=None,
                ),
            ],
        )

    with pytest.raises(ValueError, match="at most 4x"):
        GTMExperiment(
            **base,
            arms=[
                GTMExperimentArm(
                    key="control",
                    label="Control",
                    offer_summary="Control package offered at one hundred PLN.",
                    price_cents=10_000,
                ),
                GTMExperimentArm(
                    key="variant",
                    label="Variant",
                    offer_summary="Variant package offered at five hundred PLN.",
                    price_cents=50_000,
                ),
            ],
        )


def test_arm_assignment_is_balanced_deterministic_and_capped(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    experiment = _experiment(opportunity.id, max_assignments=2)
    GTMExperimentStore(engine.store).save(experiment)
    manager = GTMExperimentManager(engine)
    leads = [_lead(opportunity.id, index) for index in range(5)]

    assignments = manager.assign_for_leads(opportunity.id, leads)
    repeated = manager.assign_for_leads(opportunity.id, leads)

    assert assignments == repeated
    assert len(assignments) == 4
    counts = {"control": 0, "variant": 0}
    for arm in assignments.values():
        counts[arm.key] += 1
    assert counts == {"control": 2, "variant": 2}


def test_drafter_binds_exact_offer_arm_metadata_and_terms(tmp_path) -> None:
    opportunity = _opportunity()
    lead = _lead(opportunity.id, 1)
    experiment = _experiment(opportunity.id)
    arm = next(item for item in experiment.arms if item.key == "variant")
    provider = FakeProvider(
        [
            {
                "drafts": [
                    {
                        "lead_id": str(lead.id),
                        "subject": "Booking workflow",
                        "body": "Would a 500 PLN starter package for this workflow be worth discussing?",
                        "evidence_ids": [str(lead.evidence[0].id)],
                    }
                ]
            }
        ]
    )
    drafter = OutreachDrafter(provider, CycleBudget(max_model_calls=1))

    drafts = drafter.draft(
        opportunity,
        [],
        [lead],
        experiment=experiment,
        offer_arms={lead.id: arm},
    )

    assert len(drafts) == 1
    assert drafts[0].experiment_id == experiment.id
    assert drafts[0].experiment_arm_key == "variant"
    request = json.loads(provider.last_user)
    assigned = request["lead_arm_assignments"][str(lead.id)]
    assert assigned["price_cents"] == 50_000
    assert assigned["currency"] == "PLN"


def test_non_experiment_draft_hash_remains_historically_compatible() -> None:
    opportunity = _opportunity()
    lead = _lead(opportunity.id, 1)
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=lead.channel,
        subject="Booking workflow",
        body="Would it be useful to discuss automating this public booking workflow?",
        evidence_ids=[lead.evidence[0].id],
    )
    historical = json.dumps(
        {
            "id": str(draft.id),
            "lead_id": str(draft.lead_id),
            "opportunity_id": str(draft.opportunity_id),
            "channel": draft.channel.value,
            "subject": draft.subject,
            "body": draft.body,
            "evidence_ids": [str(item) for item in draft.evidence_ids],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert draft_hash(draft) == hashlib.sha256(historical.encode("utf-8")).hexdigest()


def test_changing_experiment_arm_after_approval_blocks_before_gateway(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    gtm = GTMStore(engine.store)
    experiment = _experiment(opportunity.id)
    GTMExperimentStore(engine.store).save(experiment)
    lead = _lead(opportunity.id, 1, stage=LeadStage.DRAFTED)
    gtm.save_lead(lead)
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=lead.channel,
        subject="Starter package",
        body="Would the proposed 500 PLN starter package be worth a short conversation?",
        evidence_ids=[lead.evidence[0].id],
        experiment_id=experiment.id,
        experiment_arm_key="variant",
    )
    gtm.save_draft(draft)
    gateway = FakeContactGateway()
    outreach = OutreachManager(engine, gateway=gateway)
    run = outreach.prepare(draft.id)
    approved = outreach.approve(run.id)
    assert approved.approval_granted is True

    gtm.save_draft(draft.model_copy(update={"experiment_arm_key": "control"}))

    with pytest.raises(OutreachError, match="draft changed after approval"):
        outreach.dispatch(run.id)
    assert gateway.calls == 0


def test_real_responses_deterministically_choose_variant_winner(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    gtm = GTMStore(engine.store)
    experiments = GTMExperimentStore(engine.store)
    experiment = _experiment(opportunity.id, max_assignments=2)
    experiments.save(experiment)

    outcomes = [
        ("control", LeadResponseKind.NOT_INTERESTED, 0),
        ("control", LeadResponseKind.NOT_INTERESTED, 0),
        ("variant", LeadResponseKind.SALE, 50_000),
        ("variant", LeadResponseKind.MEETING, 0),
    ]
    for index, (arm_key, kind, revenue_cents) in enumerate(outcomes):
        lead = _lead(opportunity.id, index + 1, stage=LeadStage.CONTACTED)
        gtm.save_lead(lead)
        draft = OutreachDraft(
            lead_id=lead.id,
            opportunity_id=opportunity.id,
            channel=lead.channel,
            subject="Offer test",
            body="This is a bounded experiment draft with explicit offer terms for evaluation.",
            evidence_ids=[lead.evidence[0].id],
            experiment_id=experiment.id,
            experiment_arm_key=arm_key,
        )
        gtm.save_draft(draft)
        run = OutreachRun(
            draft_id=draft.id,
            lead_id=lead.id,
            opportunity_id=opportunity.id,
            draft_hash=draft_hash(draft),
            status=OutreachRunStatus.WAITING_RESULT,
            approval_granted=True,
        )
        gtm.save_outreach_run(run)
        gtm.save_response(
            LeadResponse(
                run_id=run.id,
                lead_id=lead.id,
                opportunity_id=opportunity.id,
                kind=kind,
                summary=f"Recorded experiment outcome {kind.value}.",
                revenue_cents=revenue_cents,
            )
        )

    snapshot = GTMExperimentManager(engine).refresh(opportunity.id)
    completed = experiments.latest(opportunity.id)

    assert snapshot is not None
    assert snapshot.completed is True
    assert snapshot.winner_arm_key == "variant"
    assert completed is not None
    assert completed.status == GTMExperimentStatus.COMPLETED
    assert completed.winner_arm_key == "variant"

    future = _lead(opportunity.id, 99)
    assignment = GTMExperimentManager(engine).assign_for_leads(opportunity.id, [future])
    assert assignment[future.id].key == "variant"


def test_runtime_plans_experiment_after_first_response_before_search(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    gtm = GTMStore(engine.store)
    lead = _lead(opportunity.id, 1)
    gtm.save_lead(lead)
    gtm.save_response(
        LeadResponse(
            run_id=__import__("uuid").uuid4(),
            lead_id=lead.id,
            opportunity_id=opportunity.id,
            kind=LeadResponseKind.INTERESTED,
            summary="Interested enough to justify a bounded offer experiment.",
        )
    )
    provider = FakeProvider([_plan_payload()])
    prospect = NeverSearchGateway()
    runtime = GTMRuntime(
        engine,
        provider,
        CycleBudget(max_model_calls=1, max_tokens=10_000),
        prospect_gateway=prospect,
    )

    report = runtime.tick(opportunity.id)

    assert report.experiment_planned is True
    assert report.experiment_id is not None
    assert report.reason == "experiment_planned"
    assert provider.calls == 1
    assert prospect.calls == 0
