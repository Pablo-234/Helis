from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from helis.agent_spec_domain import AgentMemoryScope, AgentSpecBundle, ChildAgentSpec
from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.child_agent_store import ChildAgentArtifactStore
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


class NoCallProvider:
    calls = 0

    def complete(self, *, system: str, user: str):
        self.calls += 1
        raise AssertionError("child-agent materialization checkpoint must not call a model")


def test_advance_materializes_child_agent_before_builder_without_model_call(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    business_model = BusinessModelHypothesis(
        name="Bounded workflow service",
        payer="small service business",
        offer="Resolve ambiguous requests faster.",
        value_proposition="Reduce recurring administrative clarification work.",
        revenue_model=RevenueModel.SUBSCRIPTION,
        delivery_model=DeliveryModel.HYBRID,
        pricing=PricingHypothesis(
            currency="USD",
            low_cents=5_000,
            high_cents=10_000,
            unit="per month",
        ),
        acquisition_wedge="Start with firms already reporting slow response workflows.",
        fulfillment="Receive supplied request context and return a structured clarification.",
        automation_roles=["resolve ambiguous language"],
        human_roles=["review unusual exceptions"],
        time_to_first_revenue_days=14,
        gross_margin_pct=75,
        owner_minutes_per_week_at_scale=60,
        test_cost_cents=2_000,
        primary_risks=["customers may prefer manual clarification"],
    )
    opportunity = Opportunity(
        title="Bounded workflow service",
        problem="Service businesses lose time clarifying incomplete customer requests.",
        customer="small service businesses",
        proposed_value="Return structured clarification faster.",
        business_model=business_model,
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    validation = ValidationResult(
        run_id=uuid4(),
        experiment_id=uuid4(),
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.POSITIVE,
        confidence=0.85,
        summary="Fixture validation confirmed the workflow pain.",
        metrics={"qualified_interest_rate": 0.4},
        source="fixture",
    )
    engine.store.save_validation_result(validation)
    snapshot = architecture_input_hash(opportunity, [validation])
    capability = CapabilityNode(
        key="resolve_ambiguity",
        name="Resolve ambiguity",
        goal="Interpret supplied ambiguous requirements and identify missing facts.",
        implementation=CapabilityImplementation.AI_AGENT,
        inputs=["request context"],
        outputs=["clarified requirements"],
        success_metric="clarification accuracy on reviewed samples",
        rationale="Natural-language ambiguity is the narrow reasoning task.",
        handles_customer_data=False,
    )
    architecture = VentureArchitecture(
        opportunity_id=opportunity.id,
        input_hash=snapshot,
        capabilities=[capability],
    )
    VentureArchitectureStore(engine.store).save(architecture)
    spec = ChildAgentSpec(
        architecture_id=architecture.id,
        opportunity_id=opportunity.id,
        capability_key=capability.key,
        name=capability.name,
        goal=capability.goal,
        inputs=capability.inputs,
        outputs=capability.outputs,
        allowed_tools=[],
        memory_scope=AgentMemoryScope.NONE,
        constraints=["Use only supplied request context."],
        stop_conditions=["Missing facts are identified clearly."],
        success_metric=capability.success_metric,
        max_model_turns=3,
        max_tool_calls_per_run=0,
        handles_customer_data=False,
    )
    semantic = json.dumps(
        spec.model_dump(mode="json", exclude={"id"}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    bundle = AgentSpecBundle(
        architecture_id=architecture.id,
        opportunity_id=opportunity.id,
        architecture_input_hash=snapshot,
        bundle_hash=hashlib.sha256(semantic).hexdigest(),
        agent_specs=[spec],
    )
    AgentSpecStore(engine.store).save(bundle)
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
            model_calls=1,
            reserve_fraction=0,
            max_concentration=1,
        )
    )
    envelope = ResourceEnvelopeManager(engine).activate(plan)[0]
    provider = NoCallProvider()

    report = VentureRuntime(
        engine,
        provider,
        envelope.id,
        workspace_root=tmp_path / "build-workspaces",
        agent_workspace_root=tmp_path / "ventures",
    ).advance()

    assert report.architecture is not None and report.architecture.created is False
    assert report.agent_specs is not None and report.agent_specs.created is False
    assert report.agents is not None and report.agents.created_count == 1
    assert report.build is None
    assert provider.calls == 0
    artifacts = ChildAgentArtifactStore(engine.store).list(opportunity.id)
    assert len(artifacts) == 1
    assert (tmp_path / "ventures" / artifacts[0].manifest_path).is_file()
