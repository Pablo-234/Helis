from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import utc_now
from helis.venture_architecture_domain import CapabilityImplementation


class OrchestrationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class OrchestrationStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class OrchestrationStep(BaseModel):
    capability_key: str = Field(min_length=2, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    implementation: CapabilityImplementation
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    artifact_id: UUID | None = None
    status: OrchestrationStepStatus = OrchestrationStepStatus.PENDING
    child_run_id: UUID | None = None
    output: str = Field(default="", max_length=20_000)
    output_source: str | None = Field(default=None, min_length=2, max_length=80)
    stop_reason: str | None = Field(default=None, min_length=2, max_length=500)
    turns_used: int = Field(default=0, ge=0, le=12)


class ChildAgentOrchestrationRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    architecture_id: UUID
    bundle_id: UUID
    architecture_input_hash: str = Field(min_length=64, max_length=64)
    task: str = Field(min_length=1, max_length=12_000)
    task_hash: str = Field(min_length=64, max_length=64)
    source_key: str | None = Field(default=None, min_length=1, max_length=300)
    status: OrchestrationStatus = OrchestrationStatus.PENDING
    steps: list[OrchestrationStep] = Field(min_length=1, max_length=12)
    max_model_calls: int = Field(ge=1, le=72)
    max_tokens: int = Field(ge=1)
    max_model_cost_cents: float = Field(ge=0)
    model_calls_used: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)
    model_cost_cents_used: float = Field(default=0, ge=0)
    stop_reason: str | None = Field(default=None, min_length=2, max_length=500)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
