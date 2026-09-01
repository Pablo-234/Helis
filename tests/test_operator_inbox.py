from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from helis.child_agent_orchestration_domain import (
    ChildAgentOrchestrationRun,
    OrchestrationStatus,
    OrchestrationStep,
)
from helis.child_agent_orchestration_store import ChildAgentOrchestrationStore
from helis.commerce_domain import BillingMode, CheckoutRun, CommerceOffer
from helis.commerce_store import CommerceStore
from helis.domain import (
    DeliveryModel,
    Experiment,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentType,
    Opportunity,
    RevenueModel,
)
from helis.engine import HelisEngine
from helis.gtm_domain import (
    Lead,
    LeadChannel,
    OutreachDraft,
    OutreachRun,
    ProspectEvidence,
)
from helis.gtm_outreach import draft_hash
from helis.gtm_store import GTMStore
from helis.operator_domain import OperatorDecision, OperatorRequestKind, OperatorRequestType
from helis.operator_inbox import OperatorInbox, OperatorInboxError
from helis.preview_domain import PreviewPublishRun
from helis.preview_store import PreviewPublicationStore
from helis.self_improvement_branch_domain import BranchMaterializationRun
from helis.self_improvement_branch_store import SelfImprovementBranchStore
from helis.self_improvement_merge_domain import (
    SelfImprovementCIAttestation,
    SelfImprovementMergeRun,
    SelfImprovementMergeStatus,
)
from helis.self_improvement_merge_store import SelfImprovementMergeStore
from helis.store import HelisStore
from helis.venture_architecture_domain import CapabilityImplementation


def _fixture(tmp_path: Path):
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Operator inbox venture",
        problem="Operators need one safe place to inspect pending HELIS decisions.",
        customer="HELIS operator",
        proposed_value="Show exact approval consequences and bindings.",
    )
    engine.store.save_opportunity(opportunity)

    experiment = Experiment(
        opportunity_id=opportunity.id,
        title="Interview five operators",
        experiment_type=ExperimentType.INTERVIEW,
        hypothesis="Operators will confirm that fragmented approvals cause missed work.",
        success_metric="three positive interviews",
        success_threshold="3 of 5",
        max_cost_cents=500,
        requires_external_contact=True,
    )
    engine.store.save_experiment(experiment)
    validation = ExperimentRun(
        experiment_id=experiment.id,
        opportunity_id=opportunity.id,
        status=ExperimentRunStatus.WAITING_APPROVAL,
        adapter="approved_validation_gateway_v1",
    )
    engine.store.save_experiment_run(validation)

    preview = PreviewPublishRun(
        preview_id=uuid4(),
        opportunity_id=opportunity.id,
        artifact_hash=hashlib.sha256(b"preview").hexdigest(),
    )
    PreviewPublicationStore(engine.store).save_run(preview)

    offer = CommerceOffer(
        opportunity_id=opportunity.id,
        offer_hash=hashlib.sha256(b"offer").hexdigest(),
        name="Operator Inbox",
        description="A bounded approval dashboard for autonomous venture operations.",
        price_cents=4900,
        currency="PLN",
        pricing_unit="per month",
        billing_mode=BillingMode.SUBSCRIPTION,
        revenue_model=RevenueModel.SUBSCRIPTION,
        delivery_model=DeliveryModel.SOFTWARE,
    )
    commerce = CommerceStore(engine.store)
    commerce.save_offer(offer)
    checkout = CheckoutRun(
        offer_id=offer.id,
        opportunity_id=opportunity.id,
        offer_hash=offer.offer_hash,
    )
    commerce.save_run(checkout)

    lead = Lead(
        opportunity_id=opportunity.id,
        organization="Example Studio",
        website="https://example.com",
        contact_endpoint="hello@example.com",
        channel=LeadChannel.EMAIL,
        evidence=[
            ProspectEvidence(
                source="fixture",
                reason="The public website asks for a simpler approval workflow.",
            )
        ],
    )
    gtm = GTMStore(engine.store)
    gtm.save_lead(lead)
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=LeadChannel.EMAIL,
        contact_endpoint=lead.contact_endpoint,
        subject="Approval workflow",
        body="Your public workflow suggests that a consolidated approval inbox may help.",
        evidence_ids=[lead.evidence[0].id],
    )
    gtm.save_draft(draft)
    outreach = OutreachRun(
        draft_id=draft.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        draft_hash=draft_hash(draft),
    )
    gtm.save_outreach_run(outreach)
    return engine, opportunity, validation, preview, checkout, outreach, draft


def test_inbox_aggregates_exact_pending_requests_without_side_effects(tmp_path: Path) -> None:
    engine, opportunity, validation, preview, checkout, outreach, _ = _fixture(tmp_path)

    items = OperatorInbox(engine).list_items()

    assert [item.kind for item in items] == [
        OperatorRequestKind.OUTREACH,
        OperatorRequestKind.COMMERCE_CHECKOUT,
        OperatorRequestKind.PREVIEW_PUBLICATION,
        OperatorRequestKind.VALIDATION,
    ]
    assert {item.run_id for item in items} == {
        validation.id,
        preview.id,
        checkout.id,
        outreach.id,
    }
    assert all(item.request_type == OperatorRequestType.APPROVAL for item in items)
    assert all(item.venture_title == opportunity.title for item in items)
    assert all(item.confirmation_token and len(item.confirmation_token) == 16 for item in items)
    assert all("helis-operator approve" in item.action_command for item in items)


def test_hash_confirmed_commerce_approval_uses_existing_manager_gate(tmp_path: Path) -> None:
    engine, _, _, _, checkout, _, _ = _fixture(tmp_path)
    inbox = OperatorInbox(engine)
    item = inbox.get(f"commerce_checkout:{checkout.id}")
    assert item is not None and item.confirmation_token is not None

    with pytest.raises(OperatorInboxError, match="token does not match"):
        inbox.approve(item.key, confirmation_token="0" * 16)

    receipt = inbox.approve(item.key, confirmation_token=item.confirmation_token)

    updated = CommerceStore(engine.store).get_run(checkout.id)
    assert updated is not None and updated.approval_granted is True
    assert updated.status.value == "ready"
    assert receipt.decision == OperatorDecision.APPROVE
    assert inbox.get(item.key) is None
    assert any(
        event.event_type == "operator.inbox_approve" for event in engine.store.list_events()
    )


def test_reject_cancels_exact_outreach_and_audits_reason(tmp_path: Path) -> None:
    engine, _, _, _, _, outreach, _ = _fixture(tmp_path)
    inbox = OperatorInbox(engine)
    item = inbox.get(f"outreach:{outreach.id}")
    assert item is not None and item.confirmation_token is not None

    receipt = inbox.reject(
        item.key,
        confirmation_token=item.confirmation_token,
        reason="Personalization is not specific enough.",
    )

    updated = GTMStore(engine.store).get_outreach_run(outreach.id)
    assert updated is not None
    assert updated.status.value == "cancelled"
    assert updated.approval_granted is False
    assert updated.error == "Personalization is not specific enough."
    assert receipt.decision == OperatorDecision.REJECT
    event_types = [event.event_type for event in engine.store.list_events()]
    assert "operator.outreach_cancelled" in event_types
    assert "operator.inbox_reject" in event_types


def test_changed_request_invalidates_previous_confirmation_token(tmp_path: Path) -> None:
    engine, _, _, _, _, outreach, draft = _fixture(tmp_path)
    inbox = OperatorInbox(engine)
    item = inbox.get(f"outreach:{outreach.id}")
    assert item is not None and item.confirmation_token is not None
    GTMStore(engine.store).save_draft(
        draft.model_copy(update={"body": draft.body + " Materially changed after review."})
    )

    with pytest.raises(OperatorInboxError, match="token does not match"):
        inbox.approve(item.key, confirmation_token=item.confirmation_token)

    unchanged = GTMStore(engine.store).get_outreach_run(outreach.id)
    assert unchanged is not None and unchanged.approval_granted is False


def test_ready_non_ai_capability_appears_as_input_not_approval(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Human-gated venture",
        problem="One workflow step requires an observed owner decision.",
        customer="venture owner",
        proposed_value="Resume only after the real decision is supplied.",
    )
    engine.store.save_opportunity(opportunity)
    run = ChildAgentOrchestrationRun(
        opportunity_id=opportunity.id,
        architecture_id=uuid4(),
        bundle_id=uuid4(),
        architecture_input_hash=hashlib.sha256(b"architecture").hexdigest(),
        task="Prepare the next bounded venture step.",
        task_hash=hashlib.sha256(b"task").hexdigest(),
        status=OrchestrationStatus.BLOCKED,
        steps=[
            OrchestrationStep(
                capability_key="owner_decision",
                implementation=CapabilityImplementation.HUMAN,
            )
        ],
        max_model_calls=4,
        max_tokens=1000,
        max_model_cost_cents=2,
        stop_reason="capability_result_required:owner_decision",
    )
    ChildAgentOrchestrationStore(engine.store).create(run)

    items = OperatorInbox(engine).list_items()

    assert len(items) == 1
    assert items[0].request_type == OperatorRequestType.INPUT
    assert items[0].confirmation_token is None
    assert "supply-capability-result" in items[0].action_command
    with pytest.raises(OperatorInboxError, match="requires observed input"):
        OperatorInbox(engine).approve(items[0].key, confirmation_token="0" * 16)


def test_self_improvement_branch_and_merge_approvals_are_included_and_rejectable(
    tmp_path: Path,
) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    branch = BranchMaterializationRun(
        proposal_id=uuid4(),
        candidate_id=uuid4(),
        evaluation_id=uuid4(),
        candidate_hash="a" * 64,
        base_revision="b" * 40,
        branch_name="helis/review-operator-inbox",
    )
    SelfImprovementBranchStore(engine.store).save(branch)
    attestation = SelfImprovementCIAttestation(
        candidate_hash=branch.candidate_hash,
        base_revision=branch.base_revision,
        branch_name=branch.branch_name,
        head_revision="c" * 40,
        candidate_file_hashes={"src/helis/example.py": "d" * 64},
        passed=True,
        test_count=241,
    )
    merge = SelfImprovementMergeRun(
        branch_run_id=branch.id,
        proposal_id=branch.proposal_id,
        candidate_id=branch.candidate_id,
        candidate_hash=branch.candidate_hash,
        base_revision=branch.base_revision,
        branch_name=branch.branch_name,
        status=SelfImprovementMergeStatus.WAITING_APPROVAL,
        ci_attestation=attestation,
        ci_attestation_hash="e" * 64,
    )
    SelfImprovementMergeStore(engine.store).save(merge)
    inbox = OperatorInbox(engine)

    items = inbox.list_items()

    assert [item.kind for item in items] == [
        OperatorRequestKind.SELF_MERGE,
        OperatorRequestKind.SELF_BRANCH,
    ]
    for item in items:
        assert item.confirmation_token is not None
        receipt = inbox.reject(
            item.key,
            confirmation_token=item.confirmation_token,
            reason="Operator declined this exact self-improvement step.",
        )
        assert receipt.resulting_status == "cancelled"
    stored_branch = SelfImprovementBranchStore(engine.store).get(branch.id)
    stored_merge = SelfImprovementMergeStore(engine.store).get(merge.id)
    assert stored_branch is not None and stored_branch.status.value == "cancelled"
    assert stored_merge is not None and stored_merge.status.value == "cancelled"
