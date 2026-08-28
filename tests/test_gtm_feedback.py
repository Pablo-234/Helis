from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from helis.domain import Opportunity, VentureStage
from helis.engine import HelisEngine
from helis.gtm_decision import GTMDecisionKind, GTMDecisionStore
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
from helis.gtm_outreach import OutreachManager
from helis.gtm_store import GTMStore
from helis.portfolio_reallocation import ReallocatingPortfolioControlLoop
from helis.portfolio_scheduler import SchedulerTickReport
from helis.store import HelisStore


def _waiting_contact(engine: HelisEngine) -> tuple[Opportunity, Lead, OutreachRun]:
    opportunity = Opportunity(
        title="Automatic GTM feedback",
        problem="Small service teams repeatedly lose time preparing manual customer quotes.",
        customer="small service teams",
        proposed_value="make quote intake and response faster",
        stage=VentureStage.READY_PREVIEW,
    )
    engine.ingest(opportunity)
    evidence = ProspectEvidence(
        source="public page",
        source_url="https://feedback.example/services",
        reason="The public page describes individually prepared customer quotes.",
    )
    lead = Lead(
        opportunity_id=opportunity.id,
        organization="Feedback Company",
        website="https://feedback.example",
        contact_endpoint="https://feedback.example/contact",
        channel=LeadChannel.WEBFORM,
        evidence=[evidence],
        fit_score=8,
        stage=LeadStage.CONTACTED,
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
    now = datetime(2026, 8, 28, 10, tzinfo=UTC)
    run = OutreachRun(
        draft_id=draft.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        draft_hash="a" * 64,
        status=OutreachRunStatus.WAITING_RESULT,
        approval_granted=True,
        external_ref="dispatch-feedback",
        dispatched_at=now,
        updated_at=now,
    )
    state.save_outreach_run(run)
    return opportunity, lead, run


def test_record_response_refreshes_gtm_decision_immediately(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity, lead, run = _waiting_contact(engine)
    manager = OutreachManager(engine)
    response = LeadResponse(
        run_id=run.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        kind=LeadResponseKind.INTERESTED,
        summary="The operator wants to see a short demo.",
    )

    stored, _ = manager.record_response(response)

    decision = GTMDecisionStore(engine).latest(opportunity.id)
    assert stored.id == response.id
    assert decision is not None
    assert decision.decision == GTMDecisionKind.CONTINUE
    assert decision.metrics.resolved_outcomes == 1
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.MEASURING

    repeated, _ = manager.record_response(response)
    repeated_decision = GTMDecisionStore(engine).latest(opportunity.id)
    assert repeated.id == response.id
    assert repeated_decision is not None and repeated_decision.id == decision.id


@dataclass(slots=True)
class DecisionCheckingScheduler:
    engine: HelisEngine
    opportunity_id: object
    calls: int = 0

    def tick(self, *, max_advances: int) -> SchedulerTickReport:
        self.calls += 1
        decision = GTMDecisionStore(self.engine).latest(self.opportunity_id)
        assert decision is not None
        assert self.engine.store.get_opportunity(self.opportunity_id).stage == VentureStage.MEASURING
        return SchedulerTickReport(max_advances=max_advances)


def test_control_loop_recovers_missing_decision_before_scheduler_tick(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity, lead, run = _waiting_contact(engine)
    response = LeadResponse(
        run_id=run.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        kind=LeadResponseKind.NO_RESPONSE,
        summary="No response after the bounded observation window.",
    )
    state = GTMStore(engine.store)
    assert state.save_response(response)
    state.save_outreach_run(
        run.model_copy(
            update={
                "status": OutreachRunStatus.COMPLETED,
                "completed_at": response.created_at,
                "updated_at": response.created_at,
            }
        )
    )
    assert GTMDecisionStore(engine).latest(opportunity.id) is None

    scheduler = DecisionCheckingScheduler(engine, opportunity.id)
    report = ReallocatingPortfolioControlLoop(engine, scheduler).tick(max_advances=1)

    assert scheduler.calls == 1
    assert report.max_advances == 1
    decision = GTMDecisionStore(engine).latest(opportunity.id)
    assert decision is not None and decision.metrics.resolved_outcomes == 1
