from __future__ import annotations

import json
from pathlib import Path

from helis.child_agent_starters import create_service_intake_starter
from helis.child_agent_worker import ChildAgentWorker, WorkJobStatus
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.store import HelisStore


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


def _completed_output(summary: str) -> dict:
    return {
        "state": "completed",
        "output": json.dumps(
            {
                "summary": summary,
                "known_facts": ["customer supplied a service inquiry"],
                "missing_information": ["budget", "deadline"],
                "questions_to_ask": ["What is your budget?", "What deadline do you need?"],
                "readiness": "needs_clarification",
                "recommended_next_step": "collect missing budget and deadline",
            }
        ),
        "next_step": None,
        "needs_tool": None,
    }


def test_service_intake_starter_is_persistent_and_reused(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    workspace = tmp_path / "ventures"

    first = create_service_intake_starter(engine, workspace_root=workspace)
    second = create_service_intake_starter(engine, workspace_root=workspace)

    assert first.created is True
    assert second.created is False
    assert second.opportunity.id == first.opportunity.id
    assert second.artifact.id == first.artifact.id
    assert first.artifact.capability_key == "triage_service_inquiry"


def test_worker_processes_real_persistent_jobs_and_writes_results(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    workspace = tmp_path / "ventures"
    starter = create_service_intake_starter(engine, workspace_root=workspace)
    provider = QueueProvider([_completed_output("Inquiry one"), _completed_output("Inquiry two")])
    worker = ChildAgentWorker(
        engine,
        provider,
        starter.artifact.id,
        workspace_root=workspace,
        max_model_calls_per_job=2,
    )
    first = worker.enqueue(
        "RECORD: customer wants a website but provided no budget or deadline",
        source="test",
        source_key="row-1",
    )
    second = worker.enqueue(
        "RECORD: customer asks about automation but did not describe the workflow",
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
    starter = create_service_intake_starter(engine, workspace_root=workspace)
    worker = ChildAgentWorker(
        engine,
        QueueProvider([]),
        starter.artifact.id,
        workspace_root=workspace,
    )

    first = worker.enqueue("same record", source="csv", source_key="filehash:2")
    second = worker.enqueue("same record", source="csv", source_key="filehash:2")

    assert first.id == second.id
    assert worker.pending_count() == 1


def test_blocked_job_is_persisted_separately(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    workspace = tmp_path / "ventures"
    starter = create_service_intake_starter(engine, workspace_root=workspace)
    provider = QueueProvider(
        [
            {
                "state": "blocked",
                "output": "The supplied record is unreadable.",
                "next_step": None,
                "needs_tool": None,
            }
        ]
    )
    worker = ChildAgentWorker(
        engine,
        provider,
        starter.artifact.id,
        workspace_root=workspace,
    )
    job = worker.enqueue("RECORD: ???")

    receipt = worker.work_once()

    assert receipt is not None
    assert receipt.status == WorkJobStatus.BLOCKED
    assert (worker.queue_root / "blocked" / f"{job.id}.json").is_file()
    assert worker.receipt(job.id) is not None


def test_stale_processing_job_can_be_recovered_without_model_call(tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    workspace = tmp_path / "ventures"
    starter = create_service_intake_starter(engine, workspace_root=workspace)
    provider = QueueProvider([])
    worker = ChildAgentWorker(
        engine,
        provider,
        starter.artifact.id,
        workspace_root=workspace,
    )
    job = worker.enqueue("record that was claimed before a simulated crash")
    pending = worker.queue_root / "pending" / f"{job.id}.json"
    processing = worker.queue_root / "processing" / f"{job.id}.json"
    pending.replace(processing)
    old_timestamp = processing.stat().st_mtime - 7200
    import os

    os.utime(processing, (old_timestamp, old_timestamp))

    recovered = worker.recover_stale_processing(older_than_minutes=60)

    assert recovered == 1
    assert (worker.queue_root / "pending" / f"{job.id}.json").is_file()
    assert provider.calls == 0
