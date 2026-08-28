from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from helis.agent_spec_domain import AgentMemoryScope, AgentSpecBundle, ChildAgentSpec
from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.child_agent_factory import ChildAgentFactory
from helis.child_agent_worker import ChildAgentWorker, WorkJobStatus
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


def _generated_artifact(engine: HelisEngine, workspace: Path):
    opportunity = Opportunity(
        title="Generated venture worker fixture",
        problem="A generated online venture requires repeatable reasoning over supplied work records.",
        customer="fixture venture",
        proposed_value="Turn each supplied record into a grounded structured output.",
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    validation = ValidationResult(
        run_id=uuid4(),
        experiment_id=uuid4(),
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.POSITIVE,
        confidence=1.0,
        summary="Generic test fixture only.",
        metrics={"fixture": 1.0},
        source="fixture",
    )
    engine.store.save_validation_result(validation)
    snapshot = architecture_input_hash(opportunity, [validation])
    capability = CapabilityNode(
        key="process_record",
        name="Process generated venture record",
        goal="Transform one supplied work record into a grounded structured result.",
        implementation=CapabilityImplementation.AI_AGENT,
        inputs=["work record"],
        outputs=["structured result"],
        required_actions=[],
        success_metric="result follows the generated capability contract",
        rationale="The fixture exercises a generic child-agent worker.",
        handles_customer_data=False,
        venture_isolation_required=True,
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
        constraints=["Use only the supplied work record."],
        stop_conditions=["A grounded structured result is complete."],
        success_metric=capability.success_metric,
        max_model_turns=2,
        max_tool_calls_per_run=0,
        handles_customer_data=False,
        venture_isolation_required=True,
    )
    semantic = json.dumps(
        spec.model_dump(mode="json", exclude={"id"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    bundle = AgentSpecBundle(
        architecture_id=architecture.id,
        opportunity_id=opportunity.id,
        architecture_input_hash=snapshot,
        bundle_hash=hashlib.sha256(semantic).hexdigest(),
        agent_specs=[spec],
    )
    AgentSpecStore(engine.store).save(bundle)
    report = ChildAgentFactory(engine, workspace_root=workspace).materialize_if_needed(opportunity.id)
    assert report.blocked_reason is None
    assert len(report.artifacts) == 1
    return report.artifacts[0]


def _completed_output(summary: str) -> dict:
    return {
        "state": "completed",
        "output": json.dumps({"summary": summary, "status": "processed"}),
        "next_step": None,
        "needs_tool": None,
    }


def test_generic_generated_artifact_can_back_persistent_worker(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    workspace = tmp_path / "ventures"
    artifact = _generated_artifact(engine, workspace)

    worker = ChildAgentWorker(engine, QueueProvider([]), artifact.id, workspace_root=workspace)

    assert worker.artifact.id == artifact.id
    assert worker.artifact.capability_key == "process_record"
    assert worker.pending_count() == 0


def test_worker_processes_real_persistent_jobs_and_writes_results(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    workspace = tmp_path / "ventures"
    artifact = _generated_artifact(engine, workspace)
    provider = QueueProvider([_completed_output("record one"), _completed_output("record two")])
    worker = ChildAgentWorker(
        engine,
        provider,
        artifact.id,
        workspace_root=workspace,
        max_model_calls_per_job=2,
    )
    first = worker.enqueue("RECORD: first generated-venture work item", source="test", source_key="row-1")
    second = worker.enqueue(
        "RECORD: second generated-venture work item",
        source="test",
        source_key="row-2",
    )

    receipts = worker.work_until_empty()

    assert [item.job_id for item in receipts] == [first.id, second.id]
    assert all(item.status == WorkJobStatus.COMPLETED for item in receipts)
    assert provider.calls == 2
    assert worker.pending_count() == 0
    assert worker.receipt(first.id) is not None
    assert worker.receipt(second.id) is not None
    assert (worker.queue_root / "completed" / f"{first.id}.json").is_file()
    assert (worker.queue_root / "completed" / f"{second.id}.json").is_file()
    assert (worker.queue_root / "results" / f"{first.id}.json").is_file()


def test_source_key_makes_repeat_import_idempotent(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    workspace = tmp_path / "ventures"
    artifact = _generated_artifact(engine, workspace)
    worker = ChildAgentWorker(engine, QueueProvider([]), artifact.id, workspace_root=workspace)

    first = worker.enqueue("same record", source="csv", source_key="filehash:2")
    second = worker.enqueue("same record", source="csv", source_key="filehash:2")

    assert first.id == second.id
    assert worker.pending_count() == 1


def test_blocked_job_is_persisted_separately(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    workspace = tmp_path / "ventures"
    artifact = _generated_artifact(engine, workspace)
    provider = QueueProvider(
        [
            {
                "state": "blocked",
                "output": "The supplied record cannot be completed from available facts.",
                "next_step": None,
                "needs_tool": None,
            }
        ]
    )
    worker = ChildAgentWorker(engine, provider, artifact.id, workspace_root=workspace)
    job = worker.enqueue("RECORD: ???")

    receipt = worker.work_once()

    assert receipt is not None
    assert receipt.status == WorkJobStatus.BLOCKED
    assert (worker.queue_root / "blocked" / f"{job.id}.json").is_file()
    assert worker.receipt(job.id) is not None


def test_stale_processing_job_can_be_recovered_without_model_call(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    workspace = tmp_path / "ventures"
    artifact = _generated_artifact(engine, workspace)
    provider = QueueProvider([])
    worker = ChildAgentWorker(engine, provider, artifact.id, workspace_root=workspace)
    job = worker.enqueue("record that was claimed before a simulated crash")
    pending = worker.queue_root / "pending" / f"{job.id}.json"
    processing = worker.queue_root / "processing" / f"{job.id}.json"
    pending.replace(processing)
    old_timestamp = processing.stat().st_mtime - 7200
    os.utime(processing, (old_timestamp, old_timestamp))

    recovered = worker.recover_stale_processing(older_than_minutes=60)

    assert recovered == 1
    assert (worker.queue_root / "pending" / f"{job.id}.json").is_file()
    assert provider.calls == 0