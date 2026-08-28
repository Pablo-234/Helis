from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from helis import autopilot as autopilot_module
from helis.autopilot import (
    AutopilotPolicy,
    AutopilotStopReason,
    AutonomousOnlineVentureOperator,
)
from helis.budget import CycleBudget
from helis.domain import (
    BusinessModelHypothesis,
    DeliveryModel,
    Observation,
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
from helis.portfolio_scheduler import (
    SchedulerDisposition,
    SchedulerItem,
    SchedulerTickReport,
)
from helis.resource_envelope import EnvelopeStatus, ResourceEnvelopeManager
from helis.scout import OpportunityScout
from helis.source_registry import RegistryScanResult
from helis.store import HelisStore


class StaticScanner:
    def __init__(self, observations: list[Observation]) -> None:
        self.observations = observations

    def scan(self) -> RegistryScanResult:
        return RegistryScanResult(observations=self.observations)


class ScoutOnlyProvider:
    def __init__(self, observation_id: UUID) -> None:
        self.observation_id = observation_id
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        payload = {
            "candidates": [
                {
                    "title": "Repeated reporting workflow pain",
                    "problem": "Small online teams repeatedly assemble the same operational reports by hand.",
                    "customer": "small online service teams",
                    "proposed_value": "Reduce repetitive reporting work and turnaround time.",
                    "supporting_observation_ids": [str(self.observation_id)],
                    "tags": ["reporting"],
                    "money_models": [
                        _money_model("Remote reporting automation", "software"),
                        _money_model("On-site reporting equipment rental", "physical_ops"),
                    ],
                }
            ]
        }
        return ModelResult(content=json.dumps(payload), prompt_tokens=20, completion_tokens=20)


class ZeroIdeaPipelineProvider:
    """Returns typed model outputs while letting HELIS own all state transitions and arithmetic."""

    def __init__(self, observation_id: UUID) -> None:
        self.observation_id = observation_id
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        if "Opportunity + Monetization Scout" in system:
            content = {
                "candidates": [
                    {
                        "title": "Manual competitor-change monitoring",
                        "problem": (
                            "Small online businesses manually revisit competitor pages to notice "
                            "pricing and offer changes."
                        ),
                        "customer": "small online businesses",
                        "proposed_value": "Surface important competitor changes with less recurring work.",
                        "supporting_observation_ids": [str(self.observation_id)],
                        "tags": ["competitive_intelligence"],
                        "money_models": [
                            _money_model("Competitor change alerts", "software"),
                            _money_model("Local mystery-shopping visits", "physical_ops"),
                        ],
                    }
                ]
            }
        elif "Venture Analyst" in system:
            content = {
                "dimensions": {
                    "pain": 8,
                    "frequency": 8,
                    "willingness_to_pay": 7,
                    "market_access": 8,
                    "automation_fit": 9,
                    "speed_to_test": 9,
                    "competition_gap": 7,
                    "evidence_strength": 7,
                    "capital_efficiency": 9,
                    "execution_risk": 3,
                },
                "rationale": ["bounded fixture assessment"],
                "uncertainties": ["actual willingness to pay still needs validation"],
            }
        elif "HELIS Skeptic" in system:
            content = {
                "assumptions": [
                    {
                        "statement": "Customers care enough about competitor changes to pay.",
                        "failure_mode": "Alerts are interesting but not economically useful.",
                        "falsifier": "Qualified prospects repeatedly reject a paid pilot.",
                        "criticality": 9,
                        "uncertainty": 8,
                    }
                ],
                "contradictions": [],
                "missing_evidence": ["paid demand"],
            }
        elif "Experiment Designer" in system:
            raw = user.split("VALIDATION_INPUT:\n", 1)[1]
            opportunity_id = json.loads(raw)["opportunity"]["id"]
            content = {
                "experiments": [
                    {
                        "opportunity_id": opportunity_id,
                        "title": "Ask qualified prospects for a paid-pilot commitment",
                        "experiment_type": "interview",
                        "hypothesis": "At least some qualified prospects will commit to a paid pilot.",
                        "success_metric": "paid pilot commitments",
                        "success_threshold": ">=1 commitment",
                        "targeted_assumptions": [0],
                        "expected_information_gain": 9,
                        "effort_score": 2,
                        "max_cost_cents": 0,
                        "max_duration_hours": 48,
                        "requires_external_contact": True,
                        "requires_publication": False,
                    }
                ]
            }
        else:
            raise AssertionError(f"unexpected model call: {system[:80]}")
        return ModelResult(content=json.dumps(content), prompt_tokens=30, completion_tokens=30)


def _money_model(name: str, delivery_model: str) -> dict:
    return {
        "name": name,
        "payer": "small online businesses",
        "offer": "A bounded service that reduces recurring manual monitoring work.",
        "value_proposition": "Save recurring operator time and surface actionable changes sooner.",
        "revenue_model": "subscription",
        "delivery_model": delivery_model,
        "pricing": {"currency": "PLN", "low_cents": 5000, "high_cents": 15000, "unit": "month"},
        "acquisition_wedge": "Reach businesses already discussing the manual workflow publicly.",
        "fulfillment": "Collect configured public inputs and deliver a recurring digital result.",
        "automation_roles": ["collect public inputs", "detect material changes"],
        "human_roles": [],
        "time_to_first_revenue_days": 14,
        "gross_margin_pct": 85,
        "owner_minutes_per_week_at_scale": 30,
        "test_cost_cents": 0,
        "primary_risks": ["customers may not value the alerts enough to pay"],
    }


def _evaluated_online_venture(engine: HelisEngine) -> Opportunity:
    business_model = BusinessModelHypothesis(
        name="Online research monitor",
        payer="small online businesses",
        offer="Recurring digital monitoring report.",
        value_proposition="Reduce recurring manual research.",
        revenue_model=RevenueModel.SUBSCRIPTION,
        delivery_model=DeliveryModel.SOFTWARE,
        pricing=PricingHypothesis(currency="PLN", low_cents=5000, high_cents=10000, unit="month"),
        acquisition_wedge="Reach public online communities where the pain is discussed.",
        fulfillment="Monitor public sources and deliver digital reports.",
        automation_roles=["monitor sources"],
        human_roles=[],
        time_to_first_revenue_days=14,
        gross_margin_pct=85,
        owner_minutes_per_week_at_scale=30,
        test_cost_cents=0,
        primary_risks=["weak willingness to pay"],
    )
    opportunity = Opportunity(
        title="Online research monitor",
        problem="Small online teams repeatedly inspect the same public sources manually.",
        customer="small online teams",
        proposed_value="Reduce recurring manual research.",
        tags=["online_venture"],
        business_model=business_model,
        business_model_score=82,
        stage=VentureStage.EVALUATED,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(
                pain=8,
                frequency=8,
                willingness_to_pay=7,
                market_access=8,
                automation_fit=9,
                speed_to_test=9,
                competition_gap=7,
                evidence_strength=7,
                capital_efficiency=9,
                execution_risk=3,
            ),
            total=80,
            recommendation=Recommendation.VALIDATE,
            rationale=["fixture"],
        )
    )
    return opportunity


def test_online_only_scout_deterministically_rejects_physical_money_models() -> None:
    observation = Observation(
        text="Teams report spending hours assembling recurring reports manually.",
        source="fixture",
    )
    provider = ScoutOnlyProvider(observation.id)

    opportunities = OpportunityScout(
        provider,
        CycleBudget(max_model_calls=1, max_tokens=1000),
        online_only=True,
    ).discover([observation])

    assert len(opportunities) == 1
    assert opportunities[0].business_model is not None
    assert opportunities[0].business_model.delivery_model == DeliveryModel.SOFTWARE
    assert "online_venture" in opportunities[0].tags
    assert provider.calls == 1


def test_autopilot_bootstraps_zero_cash_portfolio_with_model_capacity(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _evaluated_online_venture(engine)
    operator = AutonomousOnlineVentureOperator(
        engine,
        ScoutOnlyProvider(UUID(int=1)),
        lambda: StaticScanner([]),
    )

    plan, created = operator._ensure_portfolio(  # noqa: SLF001 -- explicit invariant unit test
        AutopilotPolicy(cash_cents=0, portfolio_model_calls=20, reserve_fraction=0)
    )

    assert created is True
    assert plan is not None
    allocation = next(item for item in plan.allocations if item.opportunity_id == opportunity.id)
    assert allocation.cash_cents == 0
    assert allocation.model_calls > 0
    envelopes = ResourceEnvelopeManager(engine).list(status=EnvelopeStatus.ACTIVE)
    assert len(envelopes) == 1
    assert envelopes[0].opportunity_id == opportunity.id
    assert envelopes[0].model_call_limit == allocation.model_calls


def test_existing_funded_plan_is_not_refilled_by_a_later_autopilot_bootstrap(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _evaluated_online_venture(engine)
    operator = AutonomousOnlineVentureOperator(
        engine,
        ScoutOnlyProvider(UUID(int=1)),
        lambda: StaticScanner([]),
    )
    first, _ = operator._ensure_portfolio(  # noqa: SLF001
        AutopilotPolicy(portfolio_model_calls=20, reserve_fraction=0)
    )
    assert first is not None and first.allocations
    manager = ResourceEnvelopeManager(engine)
    envelope = manager.list(status=EnvelopeStatus.ACTIVE)[0]
    consumed = manager.consume(
        envelope.id,
        source="test",
        idempotency_key="one-call",
        model_calls=1,
    )
    assert consumed.model_calls_consumed == 1

    second, created = operator._ensure_portfolio(  # noqa: SLF001
        AutopilotPolicy(portfolio_model_calls=999, reserve_fraction=0)
    )

    assert created is False
    assert second is not None and second.id == first.id
    refreshed = manager.get(envelope.id)
    assert refreshed is not None
    assert refreshed.model_calls_consumed == 1
    assert refreshed.model_call_limit == envelope.model_call_limit


def test_zero_idea_autopilot_discovers_online_venture_and_runs_to_real_world_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observation = Observation(
        text=(
            "Founder says they repeatedly check competitor pricing pages by hand and often notice "
            "changes too late."
        ),
        source="fixture_web",
    )
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    provider = ZeroIdeaPipelineProvider(observation.id)

    class GateControlLoop:
        def __init__(self, controlled_engine: HelisEngine, scheduler) -> None:
            self.engine = controlled_engine

        def tick(self, *, max_advances: int) -> SchedulerTickReport:
            envelope = ResourceEnvelopeManager(self.engine).list(status=EnvelopeStatus.ACTIVE)[0]
            return SchedulerTickReport(
                plan_id=envelope.plan_id,
                max_advances=max_advances,
                attempted_advances=0,
                items=[
                    SchedulerItem(
                        envelope_id=envelope.id,
                        opportunity_id=envelope.opportunity_id,
                        priority_score=80,
                        disposition=SchedulerDisposition.SKIPPED,
                        reason="validation_waiting_approval",
                        model_calls_before=envelope.model_calls_consumed,
                        model_calls_after=envelope.model_calls_consumed,
                    )
                ],
            )

    monkeypatch.setattr(autopilot_module, "ReallocatingPortfolioControlLoop", GateControlLoop)
    operator = AutonomousOnlineVentureOperator(
        engine,
        provider,
        lambda: StaticScanner([observation]),
        workspace_root=tmp_path / "workspaces",
    )

    report = operator.run(
        AutopilotPolicy(
            cash_cents=0,
            portfolio_model_calls=30,
            reserve_fraction=0,
            max_rounds=3,
            max_advances_per_round=1,
        )
    )

    ventures = [item for item in engine.store.list_opportunities() if "online_venture" in item.tags]
    assert len(ventures) == 1
    assert ventures[0].business_model is not None
    assert ventures[0].business_model.delivery_model == DeliveryModel.SOFTWARE
    assert ventures[0].stage == VentureStage.VALIDATING
    assert report.portfolio_bootstrapped is True
    assert report.funded_ventures == 1
    assert report.stop_reason == AutopilotStopReason.REAL_WORLD_GATE
    assert report.blockers == ["validation_waiting_approval"]
    assert provider.calls == 4