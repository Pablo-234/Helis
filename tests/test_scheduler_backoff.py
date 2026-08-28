from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from helis.domain import (
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.gtm_discovery import GTMDiscoveryReport
from helis.gtm_runtime import GTMTickReport
from helis.model_provider import ModelResult
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.portfolio_scheduler import PortfolioScheduler, SchedulerDisposition
from helis.resource_envelope import ResourceEnvelopeManager
from helis.scheduler_backoff import AdaptiveSchedulerBackoff, SchedulerBackoffStore
from helis.store import HelisStore


class NeverProvider:
    def complete(self, *, system: str, user: str) -> ModelResult:
        raise AssertionError("adaptive backoff test should not invoke the model")


@dataclass(slots=True)
class MutableClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


@dataclass(slots=True)
class FakeGTM:
    reason: str


@dataclass(slots=True)
class FakeRuntimeResult:
    did_work: bool
    gtm: FakeGTM | None = None


@dataclass(slots=True)
class NoopRuntime:
    envelope_id: UUID
    calls: list[UUID]
    reason: str = "prospect_gateway_missing"

    def advance(self, *, validation_cash_cents: float = 0.0) -> FakeRuntimeResult:
        self.calls.append(self.envelope_id)
        return FakeRuntimeResult(did_work=False, gtm=FakeGTM(self.reason))


def _gtm_portfolio(tmp_path):
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Backoff GTM venture",
        problem="A recurring manual quoting workflow wastes staff time and delays customer responses.",
        customer="small service teams",
        proposed_value="reduce quote turnaround time",
        stage=VentureStage.READY_PREVIEW,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=2),
            total=86,
            recommendation=Recommendation.VALIDATE,
            rationale=["adaptive scheduler backoff fixture"],
        )
    )
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=1_000,
            currency="PLN",
            model_calls=10,
            reserve_fraction=0,
            max_concentration=0.70,
        )
    )
    envelopes = ResourceEnvelopeManager(engine)
    active = envelopes.activate(plan)
    envelope = next(item for item in active if item.opportunity_id == opportunity.id)
    return engine, opportunity, envelopes, envelope


def test_backoff_grows_and_state_change_resets_immediately(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    manager = AdaptiveSchedulerBackoff(engine)
    state = SchedulerBackoffStore(engine)
    opportunity_id = UUID("00000000-0000-0000-0000-000000000123")
    first_fingerprint = "a" * 64
    changed_fingerprint = "b" * 64
    now = datetime(2026, 8, 28, 12, tzinfo=UTC)

    first = manager.record_noop(
        opportunity_id,
        reason="approval_backlog",
        fingerprint=first_fingerprint,
        now=now,
    )
    assert first is not None
    assert first.consecutive_noops == 1
    assert first.next_eligible_at == now + timedelta(minutes=15)
    assert manager.skip_reason(
        opportunity_id,
        fingerprint=first_fingerprint,
        now=now + timedelta(minutes=1),
    ) is not None

    second = manager.record_noop(
        opportunity_id,
        reason="approval_backlog",
        fingerprint=first_fingerprint,
        now=now + timedelta(minutes=15),
    )
    assert second is not None
    assert second.consecutive_noops == 2
    assert second.next_eligible_at == now + timedelta(minutes=45)

    assert (
        manager.skip_reason(
            opportunity_id,
            fingerprint=changed_fingerprint,
            now=now + timedelta(minutes=16),
        )
        is None
    )
    assert state.get(opportunity_id) is None


def test_repeated_market_candidates_are_activity_not_progress() -> None:
    report = GTMTickReport(
        opportunity_id=UUID("00000000-0000-0000-0000-000000000321"),
        discovery=GTMDiscoveryReport(candidates_seen=12),
        reason="market_scan_no_new_signal",
    )
    assert report.did_work is False

    progressed = GTMTickReport(
        opportunity_id=report.opportunity_id,
        discovery=GTMDiscoveryReport(candidates_seen=12, leads_added=1),
        reason="discovery_completed",
    )
    assert progressed.did_work is True


def test_scheduler_skips_backed_off_gtm_without_consuming_attempt_slot(tmp_path) -> None:
    engine, opportunity, envelopes, envelope = _gtm_portfolio(tmp_path)
    calls: list[UUID] = []
    clock = MutableClock(datetime(2026, 8, 28, 12, tzinfo=UTC))

    def runtime_factory(envelope_id: UUID) -> NoopRuntime:
        return NoopRuntime(envelope_id, calls)

    scheduler = PortfolioScheduler(
        engine,
        NeverProvider(),
        runtime_factory=runtime_factory,
        clock=clock,
    )

    first = scheduler.tick(max_advances=1)
    first_item = next(item for item in first.items if item.opportunity_id == opportunity.id)
    assert first_item.disposition == SchedulerDisposition.NOOP
    assert first_item.reason == "prospect_gateway_missing"
    assert first.attempted_advances == 1
    assert calls == [envelope.id]

    clock.now += timedelta(minutes=1)
    second = scheduler.tick(max_advances=1)
    second_item = next(item for item in second.items if item.opportunity_id == opportunity.id)
    assert second_item.disposition == SchedulerDisposition.SKIPPED
    assert second_item.reason.startswith("backoff:prospect_gateway_missing:")
    assert second.attempted_advances == 0
    assert calls == [envelope.id]

    # A changed resource/state fingerprint invalidates the cooldown immediately.
    envelopes.consume(
        envelope.id,
        source="test",
        idempotency_key="fingerprint-change",
        model_calls=1,
    )
    clock.now += timedelta(minutes=1)
    third = scheduler.tick(max_advances=1)
    third_item = next(item for item in third.items if item.opportunity_id == opportunity.id)
    assert third_item.disposition == SchedulerDisposition.NOOP
    assert third.attempted_advances == 1
    assert calls == [envelope.id, envelope.id]
