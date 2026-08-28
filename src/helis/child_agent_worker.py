from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.child_agent_domain import ChildAgentRunStatus
from helis.child_agent_runtime import ChildAgentRuntime
from helis.child_agent_store import ChildAgentArtifactStore
from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider


class WorkJobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class ChildAgentWorkJob(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    artifact_id: UUID
    task: str = Field(min_length=1, max_length=12_000)
    task_hash: str = Field(min_length=64, max_length=64)
    source: str = Field(default="manual", min_length=2, max_length=80)
    source_key: str | None = Field(default=None, max_length=300)
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ChildAgentWorkReceipt(BaseModel):
    job_id: UUID
    artifact_id: UUID
    status: WorkJobStatus
    run_id: UUID | None = None
    output: str = Field(default="", max_length=20_000)
    stop_reason: str = Field(min_length=2, max_length=500)
    turns_used: int = Field(default=0, ge=0, le=12)
    completed_at: datetime = Field(default_factory=utc_now)


class ChildAgentWorker:
    """Persistent venture-local work queue for one immutable child-agent artifact."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        artifact_id: UUID,
        *,
        workspace_root: str | Path = ".helis/ventures",
        max_model_calls_per_job: int = 4,
        max_tokens_per_job: int = 12_000,
        max_model_cost_cents_per_job: float = 5.0,
    ) -> None:
        if max_model_calls_per_job < 1 or max_model_calls_per_job > 12:
            raise ValueError("max_model_calls_per_job must be between 1 and 12")
        self.engine = engine
        self.provider = provider
        self.workspace_root = Path(workspace_root)
        self.max_model_calls_per_job = max_model_calls_per_job
        self.max_tokens_per_job = max_tokens_per_job
        self.max_model_cost_cents_per_job = max_model_cost_cents_per_job
        artifact = ChildAgentArtifactStore(engine.store).get(artifact_id)
        if artifact is None:
            raise ValueError(f"child-agent artifact not found: {artifact_id}")
        self.artifact = artifact
        self.queue_root = self._safe_target(
            Path(str(artifact.opportunity_id)) / "agent-queue" / artifact.capability_key
        )
        for status in WorkJobStatus:
            (self.queue_root / status.value).mkdir(parents=True, exist_ok=True)
        (self.queue_root / "results").mkdir(parents=True, exist_ok=True)

    def enqueue(
        self,
        task: str,
        *,
        source: str = "manual",
        source_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ChildAgentWorkJob:
        normalized = task.strip()
        if not normalized:
            raise ValueError("work job task cannot be empty")
        if len(normalized) > 12_000:
            raise ValueError("work job task exceeds 12000 characters")
        if source_key is None:
            job_id = uuid4()
        else:
            job_id = uuid5(self.artifact.id, source_key)
            existing = self.find_job(job_id)
            if existing is not None:
                return existing
        job = ChildAgentWorkJob(
            id=job_id,
            artifact_id=self.artifact.id,
            task=normalized,
            task_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            source=source,
            source_key=source_key,
            metadata=metadata or {},
        )
        target = self._job_path(WorkJobStatus.PENDING, job.id)
        self._write_exclusive(target, job.model_dump_json(indent=2))
        self.engine.store.append_event(
            AuditEvent(
                event_type="venture.child_agent_job_enqueued",
                entity_id=job.id,
                data={
                    "artifact_id": str(self.artifact.id),
                    "opportunity_id": str(self.artifact.opportunity_id),
                    "capability_key": self.artifact.capability_key,
                    "task_hash": job.task_hash,
                    "source": source,
                },
            )
        )
        return job

    def find_job(self, job_id: UUID) -> ChildAgentWorkJob | None:
        for status in WorkJobStatus:
            path = self._job_path(status, job_id)
            if path.is_file():
                return ChildAgentWorkJob.model_validate_json(path.read_text(encoding="utf-8"))
        return None

    def receipt(self, job_id: UUID) -> ChildAgentWorkReceipt | None:
        path = self.queue_root / "results" / f"{job_id}.json"
        if not path.is_file():
            return None
        return ChildAgentWorkReceipt.model_validate_json(path.read_text(encoding="utf-8"))

    def pending_count(self) -> int:
        return sum(1 for _ in (self.queue_root / WorkJobStatus.PENDING.value).glob("*.json"))

    def work_once(self) -> ChildAgentWorkReceipt | None:
        pending_dir = self.queue_root / WorkJobStatus.PENDING.value
        processing_dir = self.queue_root / WorkJobStatus.PROCESSING.value
        for source_path in sorted(pending_dir.glob("*.json")):
            processing_path = processing_dir / source_path.name
            try:
                source_path.replace(processing_path)
            except FileNotFoundError:
                continue
            job = ChildAgentWorkJob.model_validate_json(
                processing_path.read_text(encoding="utf-8")
            )
            existing = self.receipt(job.id)
            if existing is not None:
                self._finish_job_file(processing_path, existing.status)
                return existing
            receipt = self._execute(job)
            result_path = self.queue_root / "results" / f"{job.id}.json"
            self._write_exclusive(result_path, receipt.model_dump_json(indent=2))
            self._finish_job_file(processing_path, receipt.status)
            return receipt
        return None

    def work_until_empty(self, *, max_jobs: int = 100) -> list[ChildAgentWorkReceipt]:
        if max_jobs < 1 or max_jobs > 10_000:
            raise ValueError("max_jobs must be between 1 and 10000")
        receipts: list[ChildAgentWorkReceipt] = []
        while len(receipts) < max_jobs:
            receipt = self.work_once()
            if receipt is None:
                break
            receipts.append(receipt)
        return receipts

    def recover_stale_processing(self, *, older_than_minutes: int = 60) -> int:
        if older_than_minutes < 1:
            raise ValueError("older_than_minutes must be positive")
        threshold = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        recovered = 0
        processing_dir = self.queue_root / WorkJobStatus.PROCESSING.value
        pending_dir = self.queue_root / WorkJobStatus.PENDING.value
        for path in processing_dir.glob("*.json"):
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified >= threshold:
                continue
            job_id = UUID(path.stem)
            if self.receipt(job_id) is not None:
                continue
            target = pending_dir / path.name
            try:
                path.replace(target)
            except FileNotFoundError:
                continue
            recovered += 1
        return recovered

    def _execute(self, job: ChildAgentWorkJob) -> ChildAgentWorkReceipt:
        budget = CycleBudget(
            max_model_calls=self.max_model_calls_per_job,
            max_tokens=self.max_tokens_per_job,
            max_cost_cents=self.max_model_cost_cents_per_job,
        )
        result = ChildAgentRuntime(
            self.engine,
            self.provider,
            budget,
            workspace_root=self.workspace_root,
        ).run(self.artifact.id, job.task)
        status_map = {
            ChildAgentRunStatus.COMPLETED: WorkJobStatus.COMPLETED,
            ChildAgentRunStatus.BLOCKED: WorkJobStatus.BLOCKED,
            ChildAgentRunStatus.FAILED: WorkJobStatus.FAILED,
        }
        receipt = ChildAgentWorkReceipt(
            job_id=job.id,
            artifact_id=self.artifact.id,
            status=status_map[result.status],
            run_id=result.id,
            output=result.output,
            stop_reason=result.stop_reason,
            turns_used=result.turns_used,
        )
        self.engine.store.append_event(
            AuditEvent(
                event_type="venture.child_agent_job_finished",
                entity_id=job.id,
                data={
                    "artifact_id": str(self.artifact.id),
                    "run_id": str(result.id),
                    "status": receipt.status.value,
                    "stop_reason": receipt.stop_reason,
                    "turns_used": receipt.turns_used,
                },
            )
        )
        return receipt

    def _finish_job_file(self, processing_path: Path, status: WorkJobStatus) -> None:
        destination = self.queue_root / status.value / processing_path.name
        processing_path.replace(destination)

    def _job_path(self, status: WorkJobStatus, job_id: UUID) -> Path:
        return self.queue_root / status.value / f"{job_id}.json"

    def _safe_target(self, relative: Path) -> Path:
        root = self.workspace_root.resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("child-agent worker path escapes venture workspace") from exc
        return target

    @staticmethod
    def _write_exclusive(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
