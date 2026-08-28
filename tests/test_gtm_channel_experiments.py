from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

import pytest

from helis.budget import CycleBudget
from helis.contact_gateway import ApprovedContactGateway, ContactGatewayAck
from helis.domain import Opportunity, VentureStage
from helis.engine import HelisEngine
from helis.gtm_channel_experiment import (
    GTMChannelExperiment,
    GTMChannelExperimentArm,
    GTMChannelExperimentManager,
    GTMChannelExperimentStatus,
    GTMChannelExperimentStore,
)
from helis.gtm_discovery import GTMDiscoveryMachine
from helis.gtm_domain import (
    Lead,
    LeadChannel,
    LeadContactOption,
    LeadResponse,
    LeadResponseKind,
    LeadStage,
    OutreachDraft,
    OutreachRun,
    OutreachRunStatus,
    ProspectEvidence,
    ProspectQuery,
    lead_contact_options,
)
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
from helis.prospect_gateway import ProspectCandidate
from helis.store import HelisStore


@dataclass(slots=True)
class NeverProvider:
    calls: int = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        raise AssertionError("model call was not expected")


@dataclass(slots=True)
class PayloadProvider:
    payloads: list[dict]
    calls: int = 0
    last_user: str = ""

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        self.last_user = user
        return ModelResult(content=json.dumps(self.payloads.pop(0)))


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
class CandidateGateway:
    candidates: list[ProspectCandidate]
    calls: int = 0
    name: str = "candidate_gateway"
    safe_destination: str = "https://prospect.example.test/search"

    def search(self, query):
        self.calls += 1
        return self.candidates


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


def _evidence(index: int = 1) -> ProspectEvidence:
    return ProspectEvidence(
        source=f"https://example.test/public-signal/{index}",
        source_url=f"https://example.test/public-signal/{index}",
        reason="The organization publicly exposes a workflow relevant to the tested venture.",
    )


def _dual_lead(
    opportunity_id,
    index: int,
    *,
    stage: LeadStage = LeadStage.QUALIFIED,
) -> Lead:
    return Lead(
        opportunity_id=opportunity_id,
        organization=f"Studio {index}",
        website=f"https://studio-{index}.example.test",
        contact_endpoint=f"hello-{index}@studio.example.test",
        channel=LeadChannel.EMAIL,
        contact_options=[
            LeadContactOption(
                channel=LeadChannel.WEBFORM,
                endpoint=f"https://studio-{index}.example.test/contact",
            )
        ],
        evidence=[_evidence(index)],
        fit_score=8,
        fit_rationale=["Public booking workflow matches the tested problem."],
        stage=stage,
    )


def _single_lead(opportunity_id, index: int) -> Lead:
    return Lead(
        opportunity_id=opportunity_id,
        organization=f"Email-only Studio {index}",
        website=f"https://email-only-{index}.example.test",
        contact_endpoint=f"hello-{index}@email-only.example.test",
        channel=LeadChannel.EMAIL,
        evidence=[_evidence(index)],
        fit_score=8,
        fit_rationale=["Public booking workflow matches the tested problem."],
        stage=LeadStage.QUALIFIED,
    )


def _commercial_completed(opportunity_id) -> GTMExperiment:
    return GTMExperiment(
        opportunity_id=opportunity_id,
        kind=GTMExperimentKind.OFFER,
        hypothesis="A bounded offer framing test completed before channel testing begins.",
        arms=[
            GTMExperimentArm(
                key="control",
                label="Control offer",
                offer_summary="Control onboarding offer used as the stable commercial baseline.",
            ),
            GTMExperimentArm(
                key="variant",
                label="Variant offer",
                offer_summary="Variant onboarding offer used in the completed commercial test.",
            ),
        ],
        status=GTMExperimentStatus.COMPLETED,
        winner_arm_key="control",
        conclusion="Control offer won the bounded commercial experiment.",
    )


def _channel_experiment(
    opportunity_id,
    *,
    max_assignments: int = 5,
) -> GTMChannelExperiment:
    return GTMChannelExperiment(
        opportunity_id=opportunity_id,
        arms=[
            GTMChannelExperimentArm(key="control", channel=LeadChannel.EMAIL),
            GTMChannelExperimentArm(key="variant", channel=LeadChannel.WEBFORM),
        ],
        max_resolved_per_arm=2,
        max_assignments_per_arm=max_assignments,
    )


def test_legacy_primary_contact_remains_a_valid_contact_option(tmp_path) -> None:
    opportunity = _opportunity()
    lead = Lead(
        opportunity_id=opportunity.id,
        organization="Legacy Studio",
        website="https://legacy.example.test",
        contact_endpoint="hello@legacy.example.test",
        channel=LeadChannel.EMAIL,
        evidence=[_evidence()],
    )

    options = lead_contact_options(lead)

    assert [(item.channel, item.endpoint) for item in options] == [
        (LeadChannel.EMAIL, "hello@legacy.example.test")
    ]


def test_channel_experiment_requires_completed_commercial_test_and_dual_channel_pool(
    tmp_path,
) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    gtm = GTMStore(engine.store)
    manager = GTMChannelExperimentManager(engine)

    first = manager.plan_if_eligible(opportunity.id)
    assert first.experiment is None

    GTMExperimentStore(engine.store).save(_commercial_completed(opportunity.id))
    gtm.save_lead(_dual_lead(opportunity.id, 1))
    still_too_small = manager.plan_if_eligible(opportunity.id)
    assert still_too_small.experiment is None

    gtm.save_lead(_dual_lead(opportunity.id, 2))
    planned = manager.plan_if_eligible(opportunity.id)

    assert planned.created is True
    assert planned.experiment is not None
    assert [arm.channel for arm in planned.experiment.arms] == [
        LeadChannel.EMAIL,
        LeadChannel.WEBFORM,
    ]


def test_channel_assignment_is_balanced_deterministic_and_capped(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    experiment = _channel_experiment(opportunity.id, max_assignments=2)
    GTMChannelExperimentStore(engine).save(experiment)
    manager = GTMChannelExperimentManager(engine)
    leads = [_dual_lead(opportunity.id, index) for index in range(5)]

    assignments = manager.assign_for_leads(opportunity.id, leads)
    repeated = manager.assign_for_leads(opportunity.id, leads)

    assert assignments == repeated
    assert len(assignments) == 4
    counts = {"control": 0, "variant": 0}
    for assignment in assignments.values():
        counts[assignment.arm_key] += 1
        assert assignment.endpoint
    assert counts == {"control": 2, "variant": 2}


def test_prospect_multi_endpoint_options_persist_on_discovered_lead(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    state = GTMStore(engine.store)
    evidence = _evidence(9)
    state.save_query(
        ProspectQuery(
            opportunity_id=opportunity.id,
            query="public booking workflow",
            target_customer="small service businesses",
            max_results=1,
        )
    )
    candidate = ProspectCandidate(
        organization="Dual Channel Studio",
        website="https://dual.example.test",
        contact_endpoint="hello@dual.example.test",
        channel=LeadChannel.EMAIL,
        contact_options=[
            LeadContactOption(
                channel=LeadChannel.WEBFORM,
                endpoint="https://dual.example.test/contact",
            )
        ],
        evidence=[evidence],
    )
    provider = PayloadProvider(
        [
            {
                "assessments": [
                    {
                        "lead_id": "00000000-0000-0000-0000-000000000000",
                        "fit_score": 0,
                        "rationale": [],
                        "supporting_evidence_ids": [],
                    }
                ]
            }
        ]
    )
    gateway = CandidateGateway([candidate])

    # The dynamic lead id makes the canned assessment intentionally non-matching. We only need
    # discovery persistence here; the single model call is bounded and cannot produce a draft.
    report = GTMDiscoveryMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=1),
        gateway,
    ).tick(opportunity.id)

    assert report.leads_added == 1
    stored = state.list_leads(opportunity.id)[0]
    options = lead_contact_options(stored)
    assert {(item.channel, item.endpoint) for item in options} == {
        (LeadChannel.EMAIL, "hello@dual.example.test"),
        (LeadChannel.WEBFORM, "https://dual.example.test/contact"),
    }


def test_active_channel_experiment_drafts_only_comparable_assigned_leads(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    state = GTMStore(engine.store)
    GTMExperimentStore(engine.store).save(_commercial_completed(opportunity.id))
    channel = _channel_experiment(opportunity.id)
    GTMChannelExperimentStore(engine).save(channel)

    dual = _dual_lead(opportunity.id, 1)
    singles = [_single_lead(opportunity.id, index) for index in (2, 3)]
    for lead in [dual, *singles]:
        state.save_lead(lead)
    state.save_query(
        ProspectQuery(
            opportunity_id=opportunity.id,
            query="public booking workflow",
            target_customer="small service businesses",
            max_results=1,
        )
    )
    assignment = GTMChannelExperimentManager(engine).assign_for_leads(opportunity.id, [dual])[dual.id]
    provider = PayloadProvider(
        [
            {
                "drafts": [
                    {
                        "lead_id": str(dual.id),
                        "subject": "Booking workflow",
                        "body": "Would it be useful to discuss automating this public booking workflow?",
                        "evidence_ids": [str(dual.evidence[0].id)],
                    }
                ]
            }
        ]
    )

    report = GTMDiscoveryMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=1),
        CandidateGateway([]),
        draft_limit=3,
        channel_experiment_manager=GTMChannelExperimentManager(engine),
    ).tick(opportunity.id)

    assert report.drafts_created == 1
    assert report.channel_experiment_assignments == 1
    request = json.loads(provider.last_user)
    assert [item["id"] for item in request["leads"]] == [str(dual.id)]
    assert request["channel_assignments"][str(dual.id)]["channel"] == assignment.channel.value
    drafts = state.list_drafts(opportunity.id)
    assert len(drafts) == 1
    assert drafts[0].lead_id == dual.id
    assert drafts[0].contact_endpoint == assignment.endpoint
    for lead in singles:
        assert state.get_draft_for_lead(lead.id) is None


def test_selected_endpoint_change_after_approval_blocks_before_gateway(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    state = GTMStore(engine.store)
    channel = _channel_experiment(opportunity.id)
    GTMChannelExperimentStore(engine).save(channel)
    lead = _dual_lead(opportunity.id, 1, stage=LeadStage.DRAFTED)
    state.save_lead(lead)
    webform = next(
        item.endpoint for item in lead_contact_options(lead) if item.channel == LeadChannel.WEBFORM
    )
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=LeadChannel.WEBFORM,
        contact_endpoint=webform,
        subject="Booking workflow",
        body="Would it be useful to discuss automating this public booking workflow?",
        evidence_ids=[lead.evidence[0].id],
        channel_experiment_id=channel.id,
        channel_experiment_arm_key="variant",
    )
    state.save_draft(draft)
    gateway = FakeContactGateway()
    outreach = OutreachManager(engine, gateway=gateway)
    run = outreach.prepare(draft.id)
    outreach.approve(run.id)

    state.save_draft(
        draft.model_copy(update={"contact_endpoint": "https://attacker.example.test/form"})
    )

    with pytest.raises(OutreachError, match="draft changed after approval"):
        outreach.dispatch(run.id)
    assert gateway.calls == 0


def test_removed_public_endpoint_after_approval_blocks_before_gateway(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    state = GTMStore(engine.store)
    channel = _channel_experiment(opportunity.id)
    GTMChannelExperimentStore(engine).save(channel)
    lead = _dual_lead(opportunity.id, 1, stage=LeadStage.DRAFTED)
    state.save_lead(lead)
    webform = next(
        item.endpoint for item in lead_contact_options(lead) if item.channel == LeadChannel.WEBFORM
    )
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=LeadChannel.WEBFORM,
        contact_endpoint=webform,
        subject="Booking workflow",
        body="Would it be useful to discuss automating this public booking workflow?",
        evidence_ids=[lead.evidence[0].id],
        channel_experiment_id=channel.id,
        channel_experiment_arm_key="variant",
    )
    state.save_draft(draft)
    gateway = FakeContactGateway()
    outreach = OutreachManager(engine, gateway=gateway)
    run = outreach.prepare(draft.id)
    outreach.approve(run.id)

    state.update_lead(lead.model_copy(update={"contact_options": []}))

    with pytest.raises(OutreachError, match="not a stored public endpoint"):
        outreach.dispatch(run.id)
    assert gateway.calls == 0


def test_same_identity_cannot_be_contacted_again_through_second_channel(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    state = GTMStore(engine.store)
    lead = _dual_lead(opportunity.id, 1, stage=LeadStage.DRAFTED)
    state.save_lead(lead)
    gateway = FakeContactGateway()
    outreach = OutreachManager(engine, gateway=gateway)
    webform = next(
        item.endpoint for item in lead_contact_options(lead) if item.channel == LeadChannel.WEBFORM
    )

    first = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=LeadChannel.WEBFORM,
        contact_endpoint=webform,
        subject="Booking workflow",
        body="Would it be useful to discuss automating this public booking workflow?",
        evidence_ids=[lead.evidence[0].id],
    )
    state.save_draft(first)
    first_run = outreach.prepare(first.id)
    outreach.approve(first_run.id)
    outreach.dispatch(first_run.id)

    second = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=LeadChannel.EMAIL,
        contact_endpoint=lead.contact_endpoint,
        subject="Booking workflow",
        body="Would it be useful to discuss automating this public booking workflow?",
        evidence_ids=[lead.evidence[0].id],
    )
    state.save_draft(second)
    second_run = outreach.prepare(second.id)
    outreach.approve(second_run.id)

    with pytest.raises(OutreachError, match="identity contact cap reached"):
        outreach.dispatch(second_run.id)
    assert gateway.calls == 1


def test_approved_gateway_receives_only_the_selected_endpoint(monkeypatch) -> None:
    opportunity = _opportunity()
    lead = _dual_lead(opportunity.id, 1, stage=LeadStage.DRAFTED)
    webform = next(
        item.endpoint for item in lead_contact_options(lead) if item.channel == LeadChannel.WEBFORM
    )
    channel_id = uuid4()
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=LeadChannel.WEBFORM,
        contact_endpoint=webform,
        subject="Booking workflow",
        body="Would it be useful to discuss automating this public booking workflow?",
        evidence_ids=[lead.evidence[0].id],
        channel_experiment_id=channel_id,
        channel_experiment_arm_key="variant",
    )
    run = OutreachRun(
        draft_id=draft.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        draft_hash=draft_hash(draft),
        status=OutreachRunStatus.READY,
        approval_granted=True,
    )
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"accepted": True, "dispatch_id": "dispatch-1", "channel": "webform"}
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr("helis.contact_gateway.urlopen", fake_urlopen)
    gateway = ApprovedContactGateway(url="https://gateway.example.test/contact")

    gateway.send(run, lead, draft)

    payload = captured["body"]
    assert isinstance(payload, dict)
    outbound_lead = payload["lead"]
    assert outbound_lead["contact_endpoint"] == webform
    assert outbound_lead["channel"] == "webform"
    assert outbound_lead["contact_options"] == []
    assert payload["constraints"]["selected_endpoint_from_approved_draft"] is True


def test_real_responses_choose_channel_winner_and_completion_is_idempotent(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    state = GTMStore(engine.store)
    experiments = GTMChannelExperimentStore(engine)
    experiment = _channel_experiment(opportunity.id, max_assignments=2)
    experiments.save(experiment)

    outcomes = [
        ("control", LeadChannel.EMAIL, LeadResponseKind.NOT_INTERESTED, 0),
        ("control", LeadChannel.EMAIL, LeadResponseKind.NOT_INTERESTED, 0),
        ("variant", LeadChannel.WEBFORM, LeadResponseKind.SALE, 50_000),
        ("variant", LeadChannel.WEBFORM, LeadResponseKind.MEETING, 0),
    ]
    for index, (arm_key, channel, kind, revenue_cents) in enumerate(outcomes, start=1):
        lead = _dual_lead(opportunity.id, index, stage=LeadStage.CONTACTED)
        state.save_lead(lead)
        endpoint = next(
            item.endpoint for item in lead_contact_options(lead) if item.channel == channel
        )
        draft = OutreachDraft(
            lead_id=lead.id,
            opportunity_id=opportunity.id,
            channel=channel,
            contact_endpoint=endpoint,
            subject="Channel test",
            body="This is a bounded first-contact channel experiment draft for evaluation.",
            evidence_ids=[lead.evidence[0].id],
            channel_experiment_id=experiment.id,
            channel_experiment_arm_key=arm_key,
        )
        state.save_draft(draft)
        run = OutreachRun(
            draft_id=draft.id,
            lead_id=lead.id,
            opportunity_id=opportunity.id,
            draft_hash=draft_hash(draft),
            status=OutreachRunStatus.WAITING_RESULT,
            approval_granted=True,
        )
        state.save_outreach_run(run)
        state.save_response(
            LeadResponse(
                run_id=run.id,
                lead_id=lead.id,
                opportunity_id=opportunity.id,
                kind=kind,
                summary=f"Recorded channel experiment outcome {kind.value}.",
                revenue_cents=revenue_cents,
            )
        )

    manager = GTMChannelExperimentManager(engine)
    snapshot = manager.refresh(opportunity.id)
    repeated = manager.refresh(opportunity.id)
    completed = experiments.latest(opportunity.id)

    assert snapshot is not None
    assert snapshot.completed is True
    assert snapshot.winner_arm_key == "variant"
    assert repeated is None
    assert completed is not None
    assert completed.status == GTMChannelExperimentStatus.COMPLETED
    assert completed.winner_arm_key == "variant"

    future = _dual_lead(opportunity.id, 99)
    assignment = manager.assign_for_leads(opportunity.id, [future])[future.id]
    assert assignment.channel == LeadChannel.WEBFORM

    with engine.store.connect() as db:
        row = db.execute(
            "SELECT COUNT(*) AS count FROM events "
            "WHERE event_type = 'gtm.channel_experiment_completed'"
        ).fetchone()
    assert row is not None
    assert row["count"] == 1


def test_runtime_can_plan_channel_experiment_with_zero_model_capacity(tmp_path) -> None:
    engine = _engine(tmp_path)
    opportunity = _opportunity()
    engine.store.save_opportunity(opportunity)
    GTMExperimentStore(engine.store).save(_commercial_completed(opportunity.id))
    state = GTMStore(engine.store)
    state.save_lead(_dual_lead(opportunity.id, 1))
    state.save_lead(_dual_lead(opportunity.id, 2))
    provider = NeverProvider()

    report = GTMRuntime(
        engine,
        provider,
        CycleBudget(max_model_calls=0),
    ).tick(opportunity.id)

    assert report.channel_experiment_planned is True
    assert report.channel_experiment_id is not None
    assert report.reason == "channel_experiment_planned"
    assert report.did_work is True
    assert provider.calls == 0
