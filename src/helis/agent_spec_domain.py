from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import utc_now
from helis.policy import ActionKind


class AgentMemoryScope(StrEnum):
    NONE = "none"
    VENTURE = "venture"
    CUSTOMER_THREAD = "customer_thread"


class AgentToolRequirement(BaseModel):
    key: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    purpose: str = Field(min_length=5, max_length=600)
    action: ActionKind
    connector_key: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    credential_alias: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )


class ChildAgentSpec(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    architecture_id: UUID
    opportunity_id: UUID
    capability_key: str = Field(min_length=2, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=3, max_length=160)
    goal: str = Field(min_length=8, max_length=1200)
    inputs: list[str] = Field(default_factory=list, max_length=8)
    outputs: list[str] = Field(default_factory=list, max_length=8)
    allowed_tools: list[AgentToolRequirement] = Field(default_factory=list, max_length=8)
    memory_scope: AgentMemoryScope = AgentMemoryScope.NONE
    constraints: list[str] = Field(min_length=1, max_length=12)
    stop_conditions: list[str] = Field(min_length=1, max_length=8)
    success_metric: str = Field(min_length=3, max_length=400)
    max_model_turns: int = Field(default=4, ge=1, le=12)
    max_tool_calls_per_run: int = Field(default=4, ge=0, le=20)
    handles_customer_data: bool = False
    venture_isolation_required: bool = True


class AgentSpecBundle(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    architecture_id: UUID
    opportunity_id: UUID
    architecture_input_hash: str = Field(min_length=64, max_length=64)
    bundle_hash: str = Field(min_length=64, max_length=64)
    agent_specs: list[ChildAgentSpec] = Field(default_factory=list, max_length=6)
    created_at: datetime = Field(default_factory=utc_now)
