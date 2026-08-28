from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from helis.agent_spec_domain import (
    AgentMemoryScope,
    AgentSpecBundle,
    AgentToolRequirement,
    ChildAgentSpec,
)
from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.budget import CycleBudget
from helis.child_agent_domain import ChildAgentRunStatus
from helis.child_agent_factory import ChildAgentArtifactTampered, ChildAgentFactory
from helis.child_agent_runtime import ChildAgentRuntime
from helis.domain import Opportunity, ValidationOutcome, ValidationResult, VentureStage
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.policy import ActionKind
from helis.store import HelisStore
from helis.venture_architecture_domain import (
    CapabilityImplementation,
    CapabilityNode,
    VentureArchitecture,
)
from helis.venture_architecture_store import VentureArchitectureStore


class QueueProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(
            content=json.dumps(self.payloads.pop(0)),
            prompt_tokens=20,
            completion_tokens=20,
        )


def _bundle_hash(spec: ChildAgentSpec, suffix: str = "") -> str:
    payload = json.dumps(
        spec.model_dump(mode="json", exclude={"id"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + suffix
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fixture(
    tmp_path: Path,
    *,
    with_tool: bool = False,
    max_turns: int = 4,
) -> tuple[HelisEngine, Opportunity, AgentSpecBundle]:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="First boot child agent",
        problem="A supplied customer request can contain ambiguous requirements.",
        customer="service business",
        proposed_value="Turn ambiguity into a structured next action.",
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    validation = ValidationResult(
        run_id=uuid4(),
        experiment_id=uuid4(),
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.POSITIVE,
        confidence=0.8,
        summary="Fixture validation result.",
        metrics={"fixture": 1.0},
        source="fixture",
    )
    engine.store.save_validation_result(validation)
    snapshot = architecture_input_hash(opportunity, [validation])
    actions = [ActionKind.RESEARCH] if with_tool else []
    capability = CapabilityNode(
        key="resolve_ambiguity",
        name="Resolve ambiguity",
        goal="Turn an ambiguous supplied request into a concise structured next action.",
        implementation=CapabilityImplementation.AI_AGENT,
        inputs=["ambiguous request"],
        outputs=["structured next action"],
        required_actions=actions,
        success_metric="output is grounded, concise and structured",
        rationale="Language ambiguity is the narrow reasoning task under test.",
        handles_customer_data=False,
        venture_isolation_required=True,
    )
    architecture = VentureArchitecture(
        opportunity_id=opportunity.id,
        input_hash=snapshot,
        capabilities=[capability],
    )
    VentureArchitectureStore(engine.store).save(architecture)
    tools = (
        [
            AgentToolRequirement(
                key="market_research",
                purpose="Retrieve evidence only when the supplied task is insufficient.",
                action=ActionKind.RESEARCH,
            )
        ]
        if with_tool
        else []
    )
    spec = ChildAgentSpec(
        architecture_id=architecture.id,
        opportunity_id=opportunity.id,
        capability_key=capability.key,
        name=capability.name,
        goal=capability.goal,
        inputs=capability.inputs,
        outputs=capability.outputs,
        allowed_tools=tools,
        memory_scope=AgentMemoryScope.NONE,
        constraints=["Use only supplied facts unless a declared future tool is required."],
        stop_conditions=["A grounded structured next action is produced."],
        success_metric=capability.success_metric,
        max_model_turns=max_turns,
        max_tool_calls_per_run=1 if with_tool else 0,
        handles_customer_data=False,
        venture_isolation_required=True,
    )
    bundle = AgentSpecBundle(
        architecture_id=architecture.id,
        opportunity_id=opportunity.id,
        architecture_input_hash=snapshot,
        bundle_hash=_bundle_hash(spec),
        agent_specs=[spec],
    )
    AgentSpecStore(engine.store).save(bundle)
    return engine, opportunity, bundle


def test_factory_materializes_exact_artifact_and_reuses_it(tmp_path) -> None:
    engine, opportunity, _ = _fixture(tmp_path)
    root = tmp_path / "ventures"
    factory = ChildAgentFactory(engine, workspace_root=root)

    first = factory.materialize_if_needed(opportunity.id)
    second = factory.materialize_if_needed(opportunity.id)

    assert first.blocked_reason is None
    assert first.created_count == 1
    assert len(first.artifacts) == 1
    assert second.created_count == 0
    assert second.artifacts[0].id == first.artifacts[0].id
    manifest = root / first.artifacts[0].manifest_path
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == first.artifacts[0].artifact_hash


def test_factory_detects_manifest_tampering_on_retry(tmp_path) -> None:
    engine, opportunity, _ = _fixture(tmp_path)
    root = tmp_path / "ventures"
    factory = ChildAgentFactory(engine, workspace_root=root)
    artifact = factory.materialize_if_needed(opportunity.id).artifacts[0]
    manifest = root / artifact.manifest_path
    manifest.write_text("tampered", encoding="utf-8")

    with pytest.raises(ChildAgentArtifactTampered, match="hash mismatch"):
        factory.materialize_if_needed(opportunity.id)


def test_reasoning_only_runtime_completes_and_persists_venture_run(tmp_path) -> None:
    engine, opportunity, _ = _fixture(tmp_path)
    root = tmp_path / "ventures"
    artifact = ChildAgentFactory(engine, workspace_root=root).materialize_if_needed(
        opportunity.id
    ).artifacts[0]
    provider = QueueProvider(
        [
            {
                "state": "completed",
                "output": "1. Ask for budget. 2. Ask for deadline. 3. Prepare the next action.",
                "next_step": None,
                "needs_tool": None,
            }
        ]
    )

    result = ChildAgentRuntime(
        engine,
        provider,
        CycleBudget(max_model_calls=4, max_tokens=1000),
        workspace_root=root,
    ).run(artifact.id, "The request omits budget and deadline.")

    assert result.status == ChildAgentRunStatus.COMPLETED
    assert result.turns_used == 1
    assert provider.calls == 1
    assert (root / result.run_path).is_file()
    assert any(
        event.event_type == "venture.child_agent_run"
        for event in engine.store.list_events()
    )


def test_declared_tool_request_blocks_without_executing_any_tool(tmp_path) -> None:
    engine, opportunity, _ = _fixture(tmp_path, with_tool=True)
    root = tmp_path / "ventures"
    artifact = ChildAgentFactory(engine, workspace_root=root).materialize_if_needed(
        opportunity.id
    ).artifacts[0]
    provider = QueueProvider(
        [
            {
                "state": "blocked",
                "output": "Fresh market evidence is required.",
                "next_step": None,
                "needs_tool": "market_research",
            }
        ]
    )

    result = ChildAgentRuntime(
        engine,
        provider,
        CycleBudget(max_model_calls=2, max_tokens=1000),
        workspace_root=root,
    ).run(artifact.id, "Compare this request with current market evidence.")

    assert result.status == ChildAgentRunStatus.BLOCKED
    assert result.stop_reason == "tool_required_unavailable:market_research"
    assert provider.calls == 1


def test_undeclared_tool_request_fails_closed(tmp_path) -> None:
    engine, opportunity, _ = _fixture(tmp_path)
    root = tmp_path / "ventures"
    artifact = ChildAgentFactory(engine, workspace_root=root).materialize_if_needed(
        opportunity.id
    ).artifacts[0]
    provider = QueueProvider(
        [
            {
                "state": "blocked",
                "output": "I want a browser.",
                "next_step": None,
                "needs_tool": "browser",
            }
        ]
    )

    result = ChildAgentRuntime(
        engine,
        provider,
        CycleBudget(max_model_calls=2, max_tokens=1000),
        workspace_root=root,
    ).run(artifact.id, "Resolve the supplied ambiguity.")

    assert result.status == ChildAgentRunStatus.FAILED
    assert result.stop_reason == "undeclared_tool_requested"


def test_runtime_enforces_spec_turn_cap(tmp_path) -> None:
    engine, opportunity, _ = _fixture(tmp_path, max_turns=2)
    root = tmp_path / "ventures"
    artifact = ChildAgentFactory(engine, workspace_root=root).materialize_if_needed(
        opportunity.id
    ).artifacts[0]
    provider = QueueProvider(
        [
            {
                "state": "continue",
                "output": "first",
                "next_step": "second",
                "needs_tool": None,
            },
            {
                "state": "continue",
                "output": "second",
                "next_step": "third",
                "needs_tool": None,
            },
        ]
    )

    result = ChildAgentRuntime(
        engine,
        provider,
        CycleBudget(max_model_calls=6, max_tokens=1000),
        workspace_root=root,
    ).run(artifact.id, "Resolve the ambiguity.")

    assert result.status == ChildAgentRunStatus.BLOCKED
    assert result.stop_reason == "turn_limit_reached"
    assert result.turns_used == 2
    assert provider.calls == 2


def test_runtime_rejects_artifact_after_spec_bundle_changes_before_model_call(tmp_path) -> None:
    engine, opportunity, bundle = _fixture(tmp_path)
    root = tmp_path / "ventures"
    artifact = ChildAgentFactory(engine, workspace_root=root).materialize_if_needed(
        opportunity.id
    ).artifacts[0]
    replacement = bundle.model_copy(
        update={
            "id": uuid4(),
            "bundle_hash": hashlib.sha256(b"replacement").hexdigest(),
        }
    )
    AgentSpecStore(engine.store).save(replacement)
    provider = QueueProvider([])

    with pytest.raises(ChildAgentArtifactTampered, match="stale"):
        ChildAgentRuntime(
            engine,
            provider,
            CycleBudget(max_model_calls=2, max_tokens=1000),
            workspace_root=root,
        ).run(artifact.id, "Resolve the ambiguity.")

    assert provider.calls == 0
