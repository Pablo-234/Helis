import json
from uuid import uuid4

from helis.domain import (
    BusinessModelHypothesis,
    DeliveryModel,
    Opportunity,
    PricingHypothesis,
    Recommendation,
    RevenueModel,
    Scorecard,
    ScoreDimensions,
    ValidationOutcome,
    ValidationResult,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.resource_envelope import ResourceEnvelopeManager
from helis.store import HelisStore
from helis.venture_architecture_store import VentureArchitectureStore
from helis.venture_runtime import VentureRuntime


class StaticProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(
            content=json.dumps(self.payload),
            prompt_tokens=10,
            completion_tokens=10,
        )


def _model() -> BusinessModelHypothesis:
    return BusinessModelHypothesis(
        name="Quote workflow service",
        payer="small service business",
        offer="Faster customer quoting with bounded automation and review.",
        value_proposition="Reduce quote turnaround and recurring administrative work.",
        revenue_model=RevenueModel.SUBSCRIPTION,
        delivery_model=DeliveryModel.HYBRID,
        pricing=PricingHypothesis(
            currency="USD",
            low_cents=5_000,
            high_cents=15_000,
            unit="per month",
        ),
        acquisition_wedge="Approach firms already reporting slow quote turnaround.",
        fulfillment="Collect inputs, prepare output, review exceptions and deliver results.",
        automation_roles=["collect structured inputs", "prepare quote draft"],
        human_roles=["review unusual high-value exceptions"],
        time_to_first_revenue_days=14,
        gross_margin_pct=75,
        owner_minutes_per_week_at_scale=60,
        test_cost_cents=5_000,
        primary_risks=["customers may not trust automated quote preparation"],
    )


def _architecture_payload() -> dict:
    return {
        "capabilities": [
            {
                "key": "capture_request",
                "name": "Capture quote request",
                "goal": "Collect structured facts needed to prepare a quote.",
                "implementation": "deterministic_automation",
                "inputs": ["customer request"],
                "outputs": ["structured quote inputs"],
                "depends_on": [],
                "required_actions": ["file_write"],
                "success_metric": "required input completion rate",
                "rationale": "Rules-based intake should not require an AI agent.",
                "handles_customer_data": True,
                "venture_isolation_required": True,
            },
            {
                "key": "resolve_ambiguity",
                "name": "Resolve ambiguous requirements",
                "goal": "Interpret ambiguous natural-language requirements and identify missing facts.",
                "implementation": "ai_agent",
                "inputs": ["structured quote inputs", "free-text notes"],
                "outputs": ["clarified requirements"],
                "depends_on": ["capture_request"],
                "required_actions": ["research"],
                "success_metric": "clarification accuracy on reviewed samples",
                "rationale": "Ambiguous language is the narrow reasoning-heavy capability.",
                "handles_customer_data": True,
                "venture_isolation_required": True,
            },
        ],
        "owner_responsibilities": ["review unusual high-value exceptions"],
        "architecture_assumptions": ["routine quote inputs can be standardized"],
    }


def test_autonomous_advance_stops_after_fresh_architecture_before_builder(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Quote workflow service",
        problem="Small service businesses lose recurring time preparing customer quotes manually.",
        customer="small service businesses",
        proposed_value="Reduce quote turnaround and administrative work.",
        business_model=_model(),
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=3),
            total=78,
            recommendation=Recommendation.VALIDATE,
            rationale=["fixture"],
        )
    )
    engine.store.save_validation_result(
        ValidationResult(
            run_id=uuid4(),
            experiment_id=uuid4(),
            opportunity_id=opportunity.id,
            outcome=ValidationOutcome.POSITIVE,
            confidence=0.85,
            summary="Prospects confirmed pain and willingness to test the proposed business model.",
            metrics={"qualified_interest_rate": 0.4},
            source="fixture",
        )
    )
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=1_000,
            model_calls=4,
            reserve_fraction=0,
            max_concentration=1,
        )
    )
    envelope = ResourceEnvelopeManager(engine).activate(plan)[0]
    provider = StaticProvider(_architecture_payload())

    report = VentureRuntime(
        engine,
        provider,
        envelope.id,
        workspace_root=tmp_path / "workspaces",
    ).advance()

    assert report.architecture is not None
    assert report.architecture.created is True
    assert report.architecture.architecture is not None
    assert report.build is None
    assert provider.calls == 1
    assert len(VentureArchitectureStore(engine.store).list(opportunity.id)) == 1
    updated = ResourceEnvelopeManager(engine).get(envelope.id)
    assert updated is not None
    assert updated.model_calls_consumed == 1
