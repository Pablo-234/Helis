from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import utc_now


class ChildAgentArtifactStatus(StrEnum):
    READY = "ready"


class ChildAgentArtifact(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bundle_id: UUID
    spec_id: UUID
    architecture_id: UUID
    opportunity_id: UUID
    capability_key: str = Field(min_length=2, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    bundle_hash: str = Field(min_length=64, max_length=64)
    spec_hash: str = Field(min_length=64, max_length=64)
    artifact_hash: str = Field(min_length=64, max_length=64)
    manifest_path: str = Field(min_length=3, max_length=500)
    status: ChildAgentArtifactStatus = ChildAgentArtifactStatus.READY
    created_at: datetime = Field(default_factory=utc_now)


class ChildAgentRunStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class ChildAgentRunResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    artifact_id: UUID
    opportunity_id: UUID
    capability_key: str
    task_hash: str = Field(min_length=64, max_length=64)
    status: ChildAgentRunStatus
    output: str = Field(default="", max_length=20_000)
    stop_reason: str = Field(min_length=2, max_length=500)
    turns_used: int = Field(ge=0, le=12)
    run_path: str = Field(min_length=3, max_length=500)
    created_at: datetime = Field(default_factory=utc_now)
