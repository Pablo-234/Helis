from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from helis.domain import (
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.portfolio_scheduler import PortfolioScheduler, SchedulerDisposition
from helis.resource_envelope import ResourceEnvelopeManager
from helis.store import HelisStore


class NeverProvider:
    def complete(self, *, system: str, user: str) -> ModelResult:
        raise AssertionError("injected scheduler runtime should be used")


@dataclass(slots=True)
class FakeGTMReport:
    reason: str


@dataclass(slots=True)
class FakeRuntimeResult:
    did_work: bool
    gtm: FakeGTMReport | None = None


@dataclass(slots=True)
class FakeRuntime:
    envelope_id: UUID
    calls: list[UUID]
    result: FakeRuntimeResult

    def advance(self, *, validation_cash_cents: float = 0.0):
        self.calls.append(self.envelope_id)
        return self.result


def _funded_gtm(tmp_path):
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Funded measuring venture",
        problem="Service teams repeatedly lose time preparing manual quotes for customers.",
        customer="small service teams",
        proposed_value="prepare clearer quotes faster",
        stage=VentureStage.MEASURING,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=3),
            total=82,
            recommendation=Recommendation.VALIDATE,
            rationale=["GTM scheduler fixture"],
        )
    )
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=1_000,
            currency="PLN",
            model_calls=0,
            reserve_fraction=0,
            max_ventures=1,
            max_concentration=1,
        )
    )
    envelope = ResourceEnvelopeManager(engine).activate(plan)[0]
    assert envelope.model_call_limit == 0
    return engine, opportunity, envelope


def test_gtm_stage_with_zero_model_capacity_still_reaches_runtime(tmp_path) -> None:
    engine, opportunity, envelope = _funded_gtm(tmp_path)
    calls: list[UUID] = []

    def factory(envelope_id: UUID):
        return FakeRuntime(
            envelope_id,
            calls,
            FakeRuntimeResult(did_work=True),
        )

    report = PortfolioScheduler(
        engine,
        NeverProvider(),
        runtime_factory=factory,
    ).tick(max_advances=1)

    assert calls == [envelope.id]
    item = next(item for item in report.items if item.opportunity_id == opportunity.id)
    assert item.disposition == SchedulerDisposition.ADVANCED
    assert report.advanced == 1


def test_gtm_runtime_noop_is_not_reported_as_progress(tmp_path) -> None:
    engine, opportunity, envelope = _funded_gtm(tmp_path)
    calls: list[UUID] = []

    def factory(envelope_id: UUID):
        return FakeRuntime(
            envelope_id,
            calls,
            FakeRuntimeResult(
                did_work=False,
                gtm=FakeGTMReport(reason="prospect_gateway_missing"),
            ),
        )

    report = PortfolioScheduler(
        engine,
        NeverProvider(),
        runtime_factory=factory,
    ).tick(max_advances=1)

    assert calls == [envelope.id]
    item = next(item for item in report.items if item.opportunity_id == opportunity.id)
    assert item.disposition == SchedulerDisposition.NOOP
    assert item.reason == "prospect_gateway_missing"
    assert report.noop == 1
    assert report.advanced == 0
