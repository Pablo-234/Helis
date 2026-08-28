import json
from uuid import uuid4

import pytest

from helis.agent_spec_domain import AgentMemoryScope, AgentToolRequirement, ChildAgentSpec
from helis.agent_spec_planner import AgentSpecPlanner
from helis.agent_spec_policy import AgentSpecPolicy, UnsafeAgentSpec
from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.budget import CycleBudget
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
from helis.policy import ActionKind
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.resource_envelope import ResourceEnvelopeManager
from helis.store import HelisStore
from helis.venture_architecture_domain import (
    CapabilityImplementation,
    CapabilityNode,
    VentureArchitecture,
)
from helis.venture_architecture_store import VentureArchitectureStore
from helis.venture_runtime import VentureRuntime


class QueueProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(
            content=json.dumps(self.payloads.pop(0)),
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
        fulfillment="Collect inputs, resolve ambiguity, review exceptions and deliver results.",
        automation_roles=["collect structured inputs", "resolve ambiguous requirements"],
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
        confidence=0.85,
        summary="Prospects confirmed pain and willingness to test the workflow.",
        metrics={"qualified_interest_rate": 0.4},
        source="fixture",
    )
    engine.store.save_validation_result(result)
    return engine, opportunity, result


def _capabilities() -> list[CapabilityNode]:
    return [
        CapabilityNode(
            key="capture_request",
            name="Capture quote request",
            goal="Collect structured facts needed to prepare a quote.",
            implementation=CapabilityImplementation.DETERMINISTIC_AUTOMATION,
            inputs=["customer request"],
            outputs=["structured quote inputs"],
            required_actions=[ActionKind.FILE_WRITE],
            success_metric="required input completion rate",
            rationale="Rules-based intake should not require an AI agent.",
            handles_customer_data=True,
        ),
        CapabilityNode(
            key="resolve_ambiguity",
            name="Resolve ambiguous requirements",
            goal="Interpret ambiguous natural-language requirements and identify missing facts.",
            implementation=CapabilityImplementation.AI_AGENT,
            inputs=["structured quote inputs", "free-text notes"],
            outputs=["clarified requirements"],
            depends_on=["capture_request"],
            required_actions=[ActionKind.RESEARCH],
            success_metric="clarification accuracy on reviewed samples",
            rationale="Ambiguous language is the narrow reasoning-heavy capability.",
            handles_customer_data=True,
        ),
        CapabilityNode(
            key="review_exception",
            name="Review high-value exception",
            goal="Review unusual high-value quote cases before customer delivery.",
            implementation=CapabilityImplementation.HUMAN,
            inputs=["clarified requirements"],
            outputs=["approved quote decision"],
            depends_on=["resolve_ambiguity"],
            success_metric="exception review completion rate",
            rationale="High-value exceptions intentionally remain human-reviewed.",
            handles_customer_data=True,
        ),
    ]


def _save_architecture(engine: HelisEngine, opportunity: Opportunity) -> VentureArchitecture:
    architecture = VentureArchitecture(
        opportunity_id=opportunity.id,
        input_hash=architecture_input_hash(
            opportunity,
            engine.store.list_validation_results(opportunity.id),
        ),
        capabilities=_capabilities(),
        owner_responsibilities=["review unusual high-value exceptions"],
    )
    VentureArchitectureStore(engine.store).save(architecture)
    return architecture


def _agent_payload() -> dict:
    return {
        "agents": [
            {
                "capability_key": "resolve_ambiguity",
                "allowed_tools": [
                    {
                        "key": "search_venture_context",
                        "purpose": "Retrieve venture-local reference material needed for clarification.",
                        "action": "research",
                        "connector_key": "venture.knowledge",
                        "credential_alias": None,
                    }
                ],
                "memory_scope": "customer_thread",
                "constraints": [
                    "Never invent missing customer facts.",
                    "Never access data outside the current venture and customer thread.",
                ],
                "stop_conditions": [
                    "Stop when requirements are sufficiently clear for the next capability.",
                    "Stop and escalate when a required fact cannot be established.",
                ],
                "max_model_turns": 4,
                "max_tool_calls_per_run": 3,
            }
        ]
    }


def _architecture_payload() -> dict:
    return {
        "capabilities": [item.model_dump(mode="json") for item in _capabilities()],
        "owner_responsibilities": ["review unusual high-value exceptions"],
        "architecture_assumptions": ["routine quote inputs can be standardized"],
    }


def test_planner_creates_specs_only_for_ai_capabilities_and_inherits_contract(tmp_path) -> None:
    engine, opportunity, _ = _validated_engine(tmp_path)
    architecture = _save_architecture(engine, opportunity)
    provider = QueueProvider([_agent_payload()])
    planner = AgentSpecPlanner(engine, provider, CycleBudget(max_model_calls=1, max_tokens=1000))

    report = planner.plan_if_needed(opportunity.id)

    assert report.created is True
    assert report.model_call_used is True
    assert report.bundle is not None
    assert len(report.bundle.agent_specs) == 1
    spec = report.bundle.agent_specs[0]
    capability = next(item for item in architecture.capabilities if item.key == "resolve_ambiguity")
    assert spec.capability_key == capability.key
    assert spec.goal == capability.goal
    assert spec.inputs == capability.inputs
    assert spec.outputs == capability.outputs
    assert spec.success_metric == capability.success_metric
    assert spec.memory_scope == AgentMemoryScope.CUSTOMER_THREAD
    assert provider.calls == 1


def test_same_architecture_reuses_agent_specs_without_second_model_call(tmp_path) -> None:
    engine, opportunity, _ = _validated_engine(tmp_path)
    _save_architecture(engine, opportunity)
    provider = QueueProvider([_agent_payload()])
    budget = CycleBudget(max_model_calls=2, max_tokens=1000)
    planner = AgentSpecPlanner(engine, provider, budget)

    first = planner.plan_if_needed(opportunity.id)
    second = planner.plan_if_needed(opportunity.id)

    assert first.bundle is not None
    assert second.bundle is not None
    assert second.created is False
    assert second.bundle.id == first.bundle.id
    assert provider.calls == 1
    assert budget.model_calls == 1


def test_architecture_without_ai_agents_persists_empty_bundle_with_zero_calls(tmp_path) -> None:
    engine, opportunity, _ = _validated_engine(tmp_path)
    architecture = VentureArchitecture(
        opportunity_id=opportunity.id,
        input_hash=architecture_input_hash(
            opportunity,
            engine.store.list_validation_results(opportunity.id),
        ),
        capabilities=[
            CapabilityNode(
                key="deterministic_workflow",
                name="Deterministic workflow",
                goal="Execute the validated workflow using deterministic rules only.",
                implementation=CapabilityImplementation.DETERMINISTIC_AUTOMATION,
                success_metric="workflow completion rate",
                rationale="The workflow requires no language-model judgment.",
            )
        ],
    )
    VentureArchitectureStore(engine.store).save(architecture)
    provider = QueueProvider([])

    report = AgentSpecPlanner(engine, provider, CycleBudget(max_model_calls=1)).plan_if_needed(
        opportunity.id
    )

    assert report.created is True
    assert report.model_call_used is False
    assert report.bundle is not None
    assert report.bundle.agent_specs == []
    assert provider.calls == 0


def test_stale_architecture_blocks_before_model_call(tmp_path) -> None:
    engine, opportunity, _ = _validated_engine(tmp_path)
    _save_architecture(engine, opportunity)
    engine.store.save_validation_result(
        ValidationResult(
            run_id=uuid4(),
            experiment_id=uuid4(),
            opportunity_id=opportunity.id,
            outcome=ValidationOutcome.POSITIVE,
            confidence=0.9,
            summary="New evidence changed the validated venture snapshot.",
            metrics={"paid_pilot_rate": 0.2},
            source="fixture",
        )
    )
    provider = QueueProvider([])

    report = AgentSpecPlanner(engine, provider, CycleBudget(max_model_calls=1)).plan_if_needed(
        opportunity.id
    )

    assert report.bundle is None
    assert report.blocked_reason == "architecture_stale"
    assert provider.calls == 0


def test_agent_spec_policy_rejects_authority_expansion_and_unsafe_memory(tmp_path) -> None:
    engine, opportunity, _ = _validated_engine(tmp_path)
    architecture = _save_architecture(engine, opportunity)
    capability = next(item for item in architecture.capabilities if item.key == "resolve_ambiguity")

    expanded = ChildAgentSpec(
        architecture_id=architecture.id,
        opportunity_id=opportunity.id,
        capability_key=capability.key,
        name=capability.name,
        goal=capability.goal,
        inputs=capability.inputs,
        outputs=capability.outputs,
        allowed_tools=[
            AgentToolRequirement(
                key="send_message",
                purpose="Attempt to contact a customer beyond the capability authority.",
                action=ActionKind.EXTERNAL_CONTACT,
            )
        ],
        memory_scope=AgentMemoryScope.CUSTOMER_THREAD,
        constraints=["Stay inside the capability."],
        stop_conditions=["Stop when clarification is complete."],
        success_metric=capability.success_metric,
        handles_customer_data=True,
    )
    with pytest.raises(UnsafeAgentSpec, match="exceeds capability authority"):
        AgentSpecPolicy().validate(architecture, [expanded])

    unsafe_memory = expanded.model_copy(
        update={
            "allowed_tools": [],
            "memory_scope": AgentMemoryScope.VENTURE,
        }
    )
    with pytest.raises(UnsafeAgentSpec, match="venture-wide conversational memory"):
        AgentSpecPolicy().validate(architecture, [unsafe_memory])


def test_autonomous_runtime_stages_architecture_then_agent_specs_before_build(tmp_path) -> None:
    engine, opportunity, _ = _validated_engine(tmp_path)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=3),
            total=78,
            recommendation=Recommendation.VALIDATE,
            rationale=["fixture"],
        )
    )
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=1_000,
            model_calls=5,
            reserve_fraction=0,
            max_concentration=1,
        )
    )
    envelope = ResourceEnvelopeManager(engine).activate(plan)[0]
    provider = QueueProvider([_architecture_payload(), _agent_payload()])
    runtime = VentureRuntime(
        engine,
        provider,
        envelope.id,
        workspace_root=tmp_path / "workspaces",
    )

    first = runtime.advance()
    second = runtime.advance()

    assert first.architecture is not None and first.architecture.created is True
    assert first.agent_specs is None
    assert first.build is None
    assert second.architecture is not None and second.architecture.created is False
    assert second.agent_specs is not None and second.agent_specs.created is True
    assert second.agent_specs.model_call_used is True
    assert second.build is None
    assert provider.calls == 2
    assert AgentSpecStore(engine.store).latest(opportunity.id) is not None
    updated = ResourceEnvelopeManager(engine).get(envelope.id)
    assert updated is not None and updated.model_calls_consumed == 2
