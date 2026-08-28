from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import utc_now
from helis.policy import ActionKind


class CapabilityImplementation(StrEnum):
    DETERMINISTIC_AUTOMATION = "deterministic_automation"
    AI_AGENT = "ai_agent"
    HUMAN = "human"
    EXTERNAL_SERVICE = "external_service"


class CapabilityNode(BaseModel):
    key: str = Field(min_length=2, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=3, max_length=160)
    goal: str = Field(min_length=8, max_length=1200)
    implementation: CapabilityImplementation
    inputs: list[str] = Field(default_factory=list, max_length=8)
    outputs: list[str] = Field(default_factory=list, max_length=8)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    required_actions: list[ActionKind] = Field(default_factory=list, max_length=8)
    success_metric: str = Field(min_length=3, max_length=400)
    rationale: str = Field(min_length=5, max_length=1200)
    handles_customer_data: bool = False
    venture_isolation_required: bool = True


class VentureArchitecture(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    input_hash: str = Field(min_length=64, max_length=64)
    capabilities: list[CapabilityNode] = Field(min_length=1, max_length=12)
    owner_responsibilities: list[str] = Field(default_factory=list, max_length=8)
    architecture_assumptions: list[str] = Field(default_factory=list, max_length=8)
    created_at: object = Field(default_factory=utc_now)
