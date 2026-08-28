from pathlib import Path
from uuid import UUID

from helis.autopilot import AutonomousOnlineVentureOperator, AutopilotPolicy, AutopilotStopReason
from helis.autopilot_cli import _scanner
from helis.domain import (
    BusinessModelHypothesis,
    DeliveryModel,
    Opportunity,
    PricingHypothesis,
    Recommendation,
    RevenueModel,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.resource_envelope import EnvelopeStatus, ResourceEnvelopeManager
from helis.source_registry import RegistryScanResult, SourceKind
from helis.store import HelisStore


class EmptyScanner:
    def scan(self) -> RegistryScanResult:
        return RegistryScanResult()


class NeverProvider:
    def complete(self, *, system: str, user: str) -> ModelResult:
        raise AssertionError("model should not be called")


def _venture(engine: HelisEngine, *, online: bool, score: float) -> Opportunity:
    model = BusinessModelHypothesis(
        name="Digital monitor" if online else "Local equipment service",
        payer="small businesses",
        offer="A useful recurring service for an expensive workflow.",
        value_proposition="Reduce recurring work and delay.",
        revenue_model=RevenueModel.SUBSCRIPTION,
        delivery_model=DeliveryModel.SOFTWARE if online else DeliveryModel.PHYSICAL_OPS,
        pricing=PricingHypothesis(currency="PLN", low_cents=5000, high_cents=10000, unit="month"),
        acquisition_wedge="Reach businesses discussing the workflow publicly.",
        fulfillment="Digital delivery" if online else "On-site physical delivery",
        automation_roles=["monitor inputs"] if online else [],
        human_roles=[] if online else ["on-site operator"],
        time_to_first_revenue_days=14,
        gross_margin_pct=80,
        owner_minutes_per_week_at_scale=60,
        test_cost_cents=0,
        primary_risks=["weak willingness to pay"],
    )
    opportunity = Opportunity(
        title=model.name,
        problem="Businesses repeatedly lose time on a recurring operational workflow.",
        customer="small businesses",
        proposed_value="Reduce recurring work.",
        tags=["online_venture"] if online else ["offline_fixture"],
        business_model=model,
        business_model_score=score,
        stage=VentureStage.EVALUATED,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(capital_efficiency=9, execution_risk=2),
            total=score,
            recommendation=Recommendation.VALIDATE,
            rationale=["fixture"],
        )
    )
    return opportunity


def test_autopilot_funds_only_online_ventures_even_when_offline_scores_higher(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    online = _venture(engine, online=True, score=70)
    offline = _venture(engine, online=False, score=99)
    operator = AutonomousOnlineVentureOperator(
        engine,
        NeverProvider(),
        EmptyScanner,
    )

    plan, created = operator._ensure_portfolio(
        AutopilotPolicy(portfolio_model_calls=20, reserve_fraction=0)
    )

    assert created is True
    assert plan is not None
    assert {item.opportunity_id for item in plan.allocations} == {online.id}
    assert offline.id not in {item.opportunity_id for item in plan.candidates}
    envelopes = ResourceEnvelopeManager(engine).list(status=EnvelopeStatus.ACTIVE)
    assert {item.opportunity_id for item in envelopes} == {online.id}


def test_missing_config_uses_builtin_public_hacker_news_source(tmp_path: Path) -> None:
    registry = _scanner(tmp_path / "does-not-exist.toml")

    assert len(registry.config.sources) == 1
    assert registry.config.sources[0].kind == SourceKind.HACKER_NEWS
    assert registry.config.sources[0].feed == "ask"


def test_existing_online_revenue_is_a_success_stop_before_scheduler(tmp_path: Path, monkeypatch) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine, online=True, score=80)
    operator = AutonomousOnlineVentureOperator(engine, NeverProvider(), EmptyScanner)
    monkeypatch.setattr(operator, "_discover", lambda policy: __import__(
        "helis.autopilot", fromlist=["AutopilotDiscoveryReport"]
    ).AutopilotDiscoveryReport())
    monkeypatch.setattr(operator, "_online_revenue", lambda currency: 1234)

    report = operator.run(AutopilotPolicy(portfolio_model_calls=10, reserve_fraction=0))

    assert report.stop_reason == AutopilotStopReason.REVENUE_OBSERVED
    assert report.revenue_cents == 1234
    assert report.scheduler_rounds == []
