from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from helis.agent_spec_domain import AgentMemoryScope, AgentSpecBundle, ChildAgentSpec
from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.child_agent_orchestration_domain import (
    OrchestrationStatus,
    OrchestrationStepStatus,
)
from helis.child_agent_orchestrator import (
    ChildAgentOrchestrator,
    UnsafeChildAgentOrchestration,
)
from helis.domain import Opportunity, ValidationOutcome, ValidationResult, VentureStage
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.store import HelisStore
from helis.venture_architecture_domain import (
    CapabilityImplementation,
    CapabilityNode,
    VentureArchitecture,
)
from helis.venture_architecture_store import VentureArchitectureStore


class RecordingProvider:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls.append({"system": system, "user": user})
        return ModelResult(
            content=json.dumps(
                {
                    "state": "completed",
                    "output": self.outputs.pop(0),
                    "next_step": None,
                    "needs_tool": None,
                }
            ),
            prompt_tokens=20,
            completion_tokens=10,
            estimated_cost_cents=0.5,
        )


def _capability(
    key: str,
    *,
    implementation: CapabilityImplementation = CapabilityImplementation.AI_AGENT,
    depends_on: list[str] | None = None,
) -> CapabilityNode:
    return CapabilityNode(
        key=key,
        name=key.replace("_", " ").title(),
        goal=f"Complete the bounded {key} capability for the supplied venture-local task.",
        implementation=implementation,
        inputs=["venture-local task"],
        outputs=[f"{key} result"],
        depends_on=depends_on or [],
        success_metric=f"{key} returns one grounded result",
        rationale=f"The fixture needs the {key} capability.",
        venture_isolation_required=True,
    )


def _fixture(
    tmp_path: Path,
    capabilities: list[CapabilityNode],
) -> tuple[HelisEngine, Opportunity, Path, AgentSpecBundle]:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Orchestrated venture fixture",
        problem="A venture needs several isolated capabilities to cooperate safely.",
        customer="fixture customer",
        proposed_value="Produce one bounded result through a capability graph.",
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    validation = ValidationResult(
        run_id=uuid4(),
        experiment_id=uuid4(),
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.POSITIVE,
        confidence=1.0,
        summary="Orchestrator fixture validation.",
        metrics={"fixture": 1.0},
        source="fixture",
    )
    engine.store.save_validation_result(validation)
    snapshot = architecture_input_hash(opportunity, [validation])
    architecture = VentureArchitecture(
        opportunity_id=opportunity.id,
        input_hash=snapshot,
        capabilities=capabilities,
    )
    VentureArchitectureStore(engine.store).save(architecture)
    specs = [
        ChildAgentSpec(
            architecture_id=architecture.id,
            opportunity_id=opportunity.id,
            capability_key=capability.key,
            name=capability.name,
            goal=capability.goal,
            inputs=capability.inputs,
            outputs=capability.outputs,
            allowed_tools=[],
            memory_scope=AgentMemoryScope.VENTURE,
            constraints=["Use only the supplied venture-local orchestration context."],
            stop_conditions=["The capability result is complete."],
            success_metric=capability.success_metric,
            max_model_turns=1,
            max_tool_calls_per_run=0,
            handles_customer_data=False,
            venture_isolation_required=True,
        )
        for capability in capabilities
        if capability.implementation == CapabilityImplementation.AI_AGENT
    ]
    semantic = json.dumps(
        [item.model_dump(mode="json", exclude={"id"}) for item in specs],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    bundle = AgentSpecBundle(
        architecture_id=architecture.id,
        opportunity_id=opportunity.id,
        architecture_input_hash=snapshot,
        bundle_hash=hashlib.sha256(semantic).hexdigest(),
        agent_specs=specs,
    )
    AgentSpecStore(engine.store).save(bundle)
    return engine, opportunity, tmp_path / "ventures", bundle


def test_orchestrator_executes_ai_dag_and_passes_only_dependency_output(tmp_path: Path) -> None:
    engine, opportunity, workspace, _ = _fixture(
        tmp_path,
        [_capability("research_need"), _capability("shape_offer", depends_on=["research_need"])],
    )
    provider = RecordingProvider(["observed pain summary", "bounded offer"])
    orchestrator = ChildAgentOrchestrator(engine, provider, workspace_root=workspace)
    run = orchestrator.start(opportunity.id, "Use only this venture input.")

    completed = orchestrator.advance(run.id)

    assert completed.status == OrchestrationStatus.COMPLETED
    assert [step.status for step in completed.steps] == [
        OrchestrationStepStatus.COMPLETED,
        OrchestrationStepStatus.COMPLETED,
    ]
    assert completed.model_calls_used == 2
    assert completed.tokens_used == 60
    assert completed.model_cost_cents_used == 1.0
    assert len(provider.calls) == 2
    assert "observed pain summary" in provider.calls[1]["user"]
    assert "bounded offer" not in provider.calls[0]["user"]
    assert any(
        event.event_type == "venture.child_agent_orchestration_step_finished"
        for event in engine.store.list_events()
    )


def test_non_ai_dependency_blocks_until_explicit_result_is_supplied(tmp_path: Path) -> None:
    engine, opportunity, workspace, _ = _fixture(
        tmp_path,
        [
            _capability(
                "owner_decision",
                implementation=CapabilityImplementation.HUMAN,
            ),
            _capability("prepare_delivery", depends_on=["owner_decision"]),
        ],
    )
    provider = RecordingProvider(["delivery plan grounded in the approved decision"])
    orchestrator = ChildAgentOrchestrator(engine, provider, workspace_root=workspace)
    run = orchestrator.start(opportunity.id, "Prepare the venture delivery plan.")

    blocked = orchestrator.advance(run.id)

    assert blocked.status == OrchestrationStatus.BLOCKED
    assert blocked.stop_reason == "capability_result_required:owner_decision"
    assert provider.calls == []

    orchestrator.supply_capability_result(
        run.id,
        "owner_decision",
        "The owner approved the narrow concierge delivery path.",
    )
    completed = orchestrator.advance(run.id)

    assert completed.status == OrchestrationStatus.COMPLETED
    assert "approved the narrow concierge" in provider.calls[0]["user"]
    assert completed.steps[0].output_source == "operator"


def test_shared_budget_caps_the_entire_graph_not_each_agent(tmp_path: Path) -> None:
    engine, opportunity, workspace, _ = _fixture(
        tmp_path,
        [_capability("first_agent"), _capability("second_agent", depends_on=["first_agent"])],
    )
    provider = RecordingProvider(["first result"])
    orchestrator = ChildAgentOrchestrator(engine, provider, workspace_root=workspace)
    run = orchestrator.start(
        opportunity.id,
        "One-call orchestration budget.",
        max_model_calls=1,
    )

    blocked = orchestrator.advance(run.id)

    assert blocked.status == OrchestrationStatus.BLOCKED
    assert blocked.model_calls_used == 1
    assert len(provider.calls) == 1
    second = next(step for step in blocked.steps if step.capability_key == "second_agent")
    assert second.status == OrchestrationStepStatus.BLOCKED
    assert second.stop_reason == "model_budget_exhausted"


def test_source_key_is_idempotent_but_cannot_be_reused_for_another_task(tmp_path: Path) -> None:
    engine, opportunity, workspace, _ = _fixture(tmp_path, [_capability("one_agent")])
    orchestrator = ChildAgentOrchestrator(
        engine,
        RecordingProvider([]),
        workspace_root=workspace,
    )

    first = orchestrator.start(opportunity.id, "same task", source_key="customer-thread-7")
    second = orchestrator.start(opportunity.id, "same task", source_key="customer-thread-7")

    assert first.id == second.id
    with pytest.raises(UnsafeChildAgentOrchestration, match="different orchestration task"):
        orchestrator.start(opportunity.id, "different task", source_key="customer-thread-7")


def test_changed_spec_bundle_blocks_resume_before_any_model_call(tmp_path: Path) -> None:
    engine, opportunity, workspace, bundle = _fixture(tmp_path, [_capability("one_agent")])
    provider = RecordingProvider([])
    orchestrator = ChildAgentOrchestrator(engine, provider, workspace_root=workspace)
    run = orchestrator.start(opportunity.id, "Attest before executing.")
    AgentSpecStore(engine.store).save(
        bundle.model_copy(
            update={
                "id": uuid4(),
                "bundle_hash": hashlib.sha256(b"replacement").hexdigest(),
            }
        )
    )

    with pytest.raises(UnsafeChildAgentOrchestration, match="stale"):
        orchestrator.advance(run.id)

    assert provider.calls == []


def test_ai_step_cannot_be_completed_by_manual_result_injection(tmp_path: Path) -> None:
    engine, opportunity, workspace, _ = _fixture(tmp_path, [_capability("one_agent")])
    orchestrator = ChildAgentOrchestrator(
        engine,
        RecordingProvider([]),
        workspace_root=workspace,
    )
    run = orchestrator.start(opportunity.id, "Do not bypass the immutable runtime.")

    with pytest.raises(UnsafeChildAgentOrchestration, match="immutable runtime"):
        orchestrator.supply_capability_result(run.id, "one_agent", "fabricated result")


def test_non_ai_result_cannot_bypass_incomplete_graph_dependency(tmp_path: Path) -> None:
    engine, opportunity, workspace, _ = _fixture(
        tmp_path,
        [
            _capability("first_decision", implementation=CapabilityImplementation.HUMAN),
            _capability(
                "second_decision",
                implementation=CapabilityImplementation.HUMAN,
                depends_on=["first_decision"],
            ),
        ],
    )
    orchestrator = ChildAgentOrchestrator(
        engine,
        RecordingProvider([]),
        workspace_root=workspace,
    )
    run = orchestrator.start(opportunity.id, "Respect the capability graph.")

    with pytest.raises(UnsafeChildAgentOrchestration, match="dependencies are incomplete"):
        orchestrator.supply_capability_result(
            run.id,
            "second_decision",
            "A result that arrived too early.",
        )
