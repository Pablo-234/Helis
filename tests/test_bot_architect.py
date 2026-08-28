import json
from uuid import uuid4

import pytest

from helis.bot_architect import BotArchitect
from helis.budget import CycleBudget
from helis.domain import (
    BusinessModelHypothesis,
    DeliveryModel,
    Opportunity,
    PricingHypothesis,
    RevenueModel,
    ValidationOutcome,
    ValidationResult,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.policy import ActionKind
from helis.store import HelisStore
from helis.venture_architecture_domain import CapabilityImplementation, CapabilityNode
from helis.venture_architecture_policy import UnsafeVentureArchitecture, VentureArchitecturePolicy
from helis.venture_architecture_store import VentureArchitectureStore


class QueueProvider:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(
            content=json.dumps(self.responses.pop(0)),
            prompt_tokens=10,
            completion_tokens=10,
        )


def _business_model() -> BusinessModelHypothesis:
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
        fulfillment="Collect quote inputs, prepare output, review exceptions and deliver results.",
        automation_roles=["collect structured inputs", "prepare quote draft"],
        human_roles=["review unusual high-value exceptions"],
        time_to_first_revenue_days=14,
        gross_margin_pct=75,
        owner_minutes_per_week_at_scale=60,
        test_cost_cents=5_000,
        primary_risks=["customers may not trust automated quote preparation"],
    )


def _validated_engine(tmp_path) -> tuple[HelisEngine, Opportunity, ValidationResult]:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Quote workflow service",
        problem="Small service businesses lose recurring time preparing customer quotes manually.",
        customer="small service businesses",
        proposed_value="Reduce quote turnaround and administrative work.",
        business_model=_business_model(),
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    result = ValidationResult(
        run_id=uuid4(),
        experiment_id=uuid4(),
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.POSITIVE,
        confidence=0.8,
        summary="Multiple prospects confirmed the workflow pain and willingness to test.",
        metrics={"qualified_interest_rate": 0.4},
        source="fixture",
    )
    engine.store.save_validation_result(result)
    return engine, opportunity, result


def _architecture_payload() -> dict:
    return {
        "capabilities": [
            {
                "key": "capture_request",
                "name": "Capture quote request",
                "goal": "Collect the structured facts needed to prepare a quote.",
                "implementation": "deterministic_automation",
                "inputs": ["customer request"],
                "outputs": ["structured quote inputs"],
                "depends_on": [],
                "required_actions": ["file_write"],
                "success_metric": "required input completion rate",
                "rationale": "Field collection is rules-based and should not require an AI agent.",
                "handles_customer_data": True,
                "venture_isolation_required": True,
            },
            {
                "key": "resolve_ambiguity",
                "name": "Resolve ambiguous requirements",
                "goal": "Interpret incomplete natural-language requirements and identify missing facts.",
                "implementation": "ai_agent",
                "inputs": ["structured quote inputs", "free-text notes"],
                "outputs": ["clarified requirements"],
                "depends_on": ["capture_request"],
                "required_actions": ["research"],
                "success_metric": "clarification accuracy on reviewed samples",
                "rationale": "Ambiguous language is the narrow part that benefits from model reasoning.",
                "handles_customer_data": True,
                "venture_isolation_required": True,
            },
            {
                "key": "deliver_quote",
                "name": "Deliver approved quote",
                "goal": "Send the final approved quote through the configured customer channel.",
                "implementation": "external_service",
                "inputs": ["approved quote"],
                "outputs": ["delivery receipt"],
                "depends_on": ["resolve_ambiguity"],
                "required_actions": ["external_contact"],
                "success_metric": "successful delivery rate",
                "rationale": "Transport should be an external gated capability, not rebuilt by the agent.",
                "handles_customer_data": True,
                "venture_isolation_required": True,
            },
        ],
        "owner_responsibilities": ["review unusual high-value exceptions"],
        "architecture_assumptions": ["quote inputs can be standardized enough for routine cases"],
    }


def test_architect_requires_validated_money_model_without_model_call(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Unvalidated idea",
        problem="A sufficiently specific unvalidated customer workflow problem exists here.",
        customer="service businesses",
        proposed_value="Improve the workflow.",
    )
    engine.store.save_opportunity(opportunity)
    provider = QueueProvider([])

    report = BotArchitect(engine, provider, CycleBudget(max_model_calls=1)).plan_if_needed(
        opportunity.id
    )

    assert report.architecture is None
    assert report.blocked_reason == "venture_not_validated"
    assert provider.calls == 0


def test_architect_persists_snapshot_and_reuses_it_without_second_model_call(tmp_path) -> None:
    engine, opportunity, _ = _validated_engine(tmp_path)
    provider = QueueProvider([_architecture_payload()])
    budget = CycleBudget(max_model_calls=2, max_tokens=1000)
    architect = BotArchitect(engine, provider, budget)

    first = architect.plan_if_needed(opportunity.id)
    second = architect.plan_if_needed(opportunity.id)

    assert first.created is True
    assert first.architecture is not None
    assert second.created is False
    assert second.architecture is not None
    assert second.architecture.id == first.architecture.id
    assert provider.calls == 1
    assert budget.model_calls == 1
    assert len(VentureArchitectureStore(engine.store).list(opportunity.id)) == 1
    assert any(
        event.event_type == "venture.architecture_planned"
        for event in engine.store.list_events()
    )


def test_new_validation_result_invalidates_architecture_snapshot(tmp_path) -> None:
    engine, opportunity, _ = _validated_engine(tmp_path)
    provider = QueueProvider([_architecture_payload(), _architecture_payload()])
    budget = CycleBudget(max_model_calls=2, max_tokens=1000)
    architect = BotArchitect(engine, provider, budget)

    first = architect.plan_if_needed(opportunity.id)
    engine.store.save_validation_result(
        ValidationResult(
            run_id=uuid4(),
            experiment_id=uuid4(),
            opportunity_id=opportunity.id,
            outcome=ValidationOutcome.POSITIVE,
            confidence=0.9,
            summary="A second validation result changed the evidence snapshot.",
            metrics={"paid_pilot_rate": 0.2},
            source="fixture",
        )
    )
    second = architect.plan_if_needed(opportunity.id)

    assert first.architecture is not None
    assert second.architecture is not None
    assert second.created is True
    assert second.architecture.input_hash != first.architecture.input_hash
    assert provider.calls == 2
    assert len(VentureArchitectureStore(engine.store).list(opportunity.id)) == 2


def test_architecture_policy_rejects_self_modify_and_cycles() -> None:
    policy = VentureArchitecturePolicy()
    self_modifying = CapabilityNode(
        key="rewrite_core",
        name="Rewrite core",
        goal="Change HELIS itself from a child venture capability.",
        implementation=CapabilityImplementation.AI_AGENT,
        required_actions=[ActionKind.SELF_MODIFY],
        success_metric="not applicable",
        rationale="This must be rejected regardless of model preference.",
    )
    with pytest.raises(UnsafeVentureArchitecture, match="self-modification"):
        policy.validate([self_modifying])

    first = CapabilityNode(
        key="first_step",
        name="First step",
        goal="Perform the first operation in a deliberately cyclic graph.",
        implementation=CapabilityImplementation.DETERMINISTIC_AUTOMATION,
        depends_on=["second_step"],
        success_metric="completion",
        rationale="Fixture for cycle rejection.",
    )
    second = CapabilityNode(
        key="second_step",
        name="Second step",
        goal="Perform the second operation in a deliberately cyclic graph.",
        implementation=CapabilityImplementation.DETERMINISTIC_AUTOMATION,
        depends_on=["first_step"],
        success_metric="completion",
        rationale="Fixture for cycle rejection.",
    )
    with pytest.raises(UnsafeVentureArchitecture, match="acyclic"):
        policy.validate([first, second])


def test_sensitive_action_requirements_are_descriptors_not_grants(tmp_path) -> None:
    engine, opportunity, _ = _validated_engine(tmp_path)
    payload = _architecture_payload()
    payload["capabilities"][2]["required_actions"] = [
        "external_contact",
        "credential_access",
        "spend",
    ]
    provider = QueueProvider([payload])

    report = BotArchitect(engine, provider, CycleBudget(max_model_calls=1)).plan_if_needed(
        opportunity.id
    )

    assert report.architecture is not None
    delivery = next(
        item for item in report.architecture.capabilities if item.key == "deliver_quote"
    )
    assert set(delivery.required_actions) == {
        ActionKind.EXTERNAL_CONTACT,
        ActionKind.CREDENTIAL_ACCESS,
        ActionKind.SPEND,
    }
    assert provider.calls == 1
