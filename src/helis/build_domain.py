from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import utc_now


class BuildRuntime(StrEnum):
    STATIC_WEB = "static_web"
    PYTHON_STDLIB = "python_stdlib"


class BuildRunStatus(StrEnum):
    PLANNED = "planned"
    GENERATING = "generating"
    GENERATED = "generated"
    TESTING = "testing"
    TESTED = "tested"
    FAILED = "failed"
    BLOCKED = "blocked"


class SandboxStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class BuildSpec(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    product_name: str = Field(min_length=2, max_length=100)
    objective: str = Field(min_length=10, max_length=1000)
    target_user: str = Field(min_length=2, max_length=300)
    core_flows: list[str] = Field(min_length=1, max_length=6)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=10)
    non_goals: list[str] = Field(default_factory=list, max_length=10)
    runtime: BuildRuntime
    created_at: datetime = Field(default_factory=utc_now)


class BuildFile(BaseModel):
    path: str = Field(min_length=1, max_length=240)
    content: str
    role: str = Field(default="source", max_length=50)


class BuildBundle(BaseModel):
    spec_id: UUID
    files: list[BuildFile] = Field(min_length=1)


class SandboxReport(BaseModel):
    status: SandboxStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = Field(default=0, ge=0)
    verifier: str = Field(min_length=1)


class BuildRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    spec_id: UUID
    opportunity_id: UUID
    status: BuildRunStatus = BuildRunStatus.PLANNED
    workspace_path: str | None = None
    file_count: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    bundle_digest: str | None = None
    sandbox: SandboxReport | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
