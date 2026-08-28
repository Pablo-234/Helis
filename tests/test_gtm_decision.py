from __future__ import annotations

from datetime import UTC, datetime

from helis.domain import Opportunity, VentureStage
from helis.engine import HelisEngine
from helis.gtm_decision import GTMDecisionEngine, GTMDecisionKind
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
from helis.gtm_metrics import collect_gtm_metrics
from helis.gtm_store import GTMStore
from helis.store import HelisStore


def _venture(engine: HelisEngine) -> Opportunity:
    opportunity = Opportunity(
        title="Measured GTM venture",
        problem="Small service teams repeatedly lose time on manual customer quoting workflows.",
        customer="small service teams",
        proposed_value="make quote intake and response faster",
        stage=VentureStage.READY_PREVIEW,
    )
    engine.ingest(opportunity)
    return opportunity


def _resolved_contact(
    engine: HelisEngine,
    opportunity: Opportunity,
    index: int,
    kind: LeadResponseKind,
    *,
    revenue_cents: int = 0,
    currency: str = "PLN",
) -> None:
    state = GTMStore(engine.store)
    evidence = ProspectEvidence(
        source="public page",
        source_url=f"https://company-{index}.example/services",
        reason="The public page describes a manual quote workflow for customer projects.",
    )
    lead = Lead(
        opportunity_id=opportunity.id,
        organization=f"Company {index}",
        website=f"https://company-{index}.example",
        contact_endpoint=f"https://company-{index}.example/contact",
        channel=LeadChannel.WEBFORM,
        evidence=[evidence],
        fit_score=8,
        stage=LeadStage.CONTACTED,
    )
    assert state.save_lead(lead)
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        channel=lead.channel,
        body="Evidence-bound first contact message with a clear and respectful opt-out path.",
        evidence_ids=[evidence.id],
    )
    state.save_draft(draft)
    timestamp = datetime(2026, 8, 28, 10, index % 60, tzinfo=UTC)
    run = OutreachRun(
        draft_id=draft.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        draft_hash="a" * 64,
        status=OutreachRunStatus.COMPLETED,
        approval_granted=True,
        external_ref=f"dispatch-{index}",
        dispatched_at=timestamp,
        completed_at=timestamp,
        updated_at=timestamp,
    )
    state.save_outreach_run(run)
    response = LeadResponse(
        run_id=run.id,
        lead_id=lead.id,
        opportunity_id=opportunity.id,
        kind=kind,
        summary=f"Resolved GTM outcome {kind.value}.",
        revenue_cents=revenue_cents,
        currency=currency,
        created_at=timestamp,
    )
    state.save_response(response)
    if revenue_cents:
        from helis.gtm_domain import RevenueEvent

        state.save_revenue(
            RevenueEvent(
                opportunity_id=opportunity.id,
                lead_id=lead.id,
                response_id=response.id,
                amount_cents=revenue_cents,
                currency=currency,
                source="test",
                external_ref=run.external_ref,
            )
        )


def test_small_sample_continues_and_snapshot_is_idempotent(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    for index in range(7):
        _resolved_contact(engine, opportunity, index, LeadResponseKind.NO_RESPONSE)

    decision_engine = GTMDecisionEngine(engine)
    first = decision_engine.evaluate(opportunity.id)
    second = decision_engine.evaluate(opportunity.id)

    assert first.decision == GTMDecisionKind.CONTINUE
    assert first.id == second.id
    assert first.metrics.resolved_outcomes == 7
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.MEASURING


def test_weak_signal_pauses_before_hard_kill(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    for index in range(8):
        _resolved_contact(engine, opportunity, index, LeadResponseKind.NO_RESPONSE)

    decision = GTMDecisionEngine(engine).evaluate(opportunity.id)

    assert decision.decision == GTMDecisionKind.PAUSE
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.PAUSED


def test_zero_positive_signal_eventually_kills(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    for index in range(15):
        kind = LeadResponseKind.NOT_INTERESTED if index % 3 == 0 else LeadResponseKind.NO_RESPONSE
        _resolved_contact(engine, opportunity, index, kind)

    decision = GTMDecisionEngine(engine).evaluate(opportunity.id)

    assert decision.decision == GTMDecisionKind.KILL
    assert decision.confidence >= 0.9
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.KILLED


def test_repeatable_paid_demand_scales_and_keeps_currency_separate(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    outcomes = [
        (LeadResponseKind.SALE, 49_900, "PLN"),
        (LeadResponseKind.SALE, 100_00, "EUR"),
        (LeadResponseKind.INTERESTED, 0, "PLN"),
        (LeadResponseKind.MEETING, 0, "PLN"),
        (LeadResponseKind.NOT_INTERESTED, 0, "PLN"),
        (LeadResponseKind.NO_RESPONSE, 0, "PLN"),
    ]
    for index, (kind, revenue, currency) in enumerate(outcomes):
        _resolved_contact(
            engine,
            opportunity,
            index,
            kind,
            revenue_cents=revenue,
            currency=currency,
        )

    state = GTMStore(engine.store)
    metrics = collect_gtm_metrics(state, opportunity.id)
    decision = GTMDecisionEngine(engine).evaluate(opportunity.id)

    assert metrics.sales == 2
    assert metrics.positive_rate >= 0.6
    assert metrics.revenue_by_currency == {"PLN": 49_900, "EUR": 10_000}
    assert decision.decision == GTMDecisionKind.SCALE
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.SCALING
