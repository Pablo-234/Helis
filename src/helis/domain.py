from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VentureStage(StrEnum):
    DISCOVERED = "discovered"
    EVALUATED = "evaluated"
    VALIDATING = "validating"
    BUILDING = "building"
    LAUNCHED = "launched"
    MEASURING = "measuring"
    SCALING = "scaling"
    PIVOTED = "pivoted"
    PAUSED = "paused"
    KILLED = "killed"


class Recommendation(StrEnum):
    EXPLORE = "explore"
    VALIDATE = "validate"
    KILL = "kill"


class EvidenceKind(StrEnum):
    CUSTOMER_PAIN = "customer_pain"
    WORKFLOW = "workflow"
    PRICING = "pricing"
    COMPETITION = "competition"
    WILLINGNESS_TO_PAY = "willingness_to_pay"
    DEMAND = "demand"
    TREND = "trend"
    OTHER = "other"


class Evidence(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    kind: EvidenceKind
    claim: str = Field(min_length=3)
    source: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    observed_at: datetime = Field(default_factory=utc_now)


class Opportunity(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=3, max_length=200)
    problem: str = Field(min_length=10)
    customer: str = Field(min_length=2)
    proposed_value: str = Field(min_length=3)
    evidence: list[Evidence] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    discovered_at: datetime = Field(default_factory=utc_now)
    stage: VentureStage = VentureStage.DISCOVERED


class ScoreDimensions(BaseModel):
    pain: float = 5
    frequency: float = 5
    willingness_to_pay: float = 5
    market_access: float = 5
    automation_fit: float = 5
    speed_to_test: float = 5
    competition_gap: float = 5
    evidence_strength: float = 5
    capital_efficiency: float = 5
    execution_risk: float = 5

    @field_validator("*", mode="after")
    @classmethod
    def dimensions_are_0_to_10(cls, value: float) -> float:
        if not 0 <= value <= 10:
            raise ValueError("score dimensions must be between 0 and 10")
        return value


class Scorecard(BaseModel):
    opportunity_id: UUID
    dimensions: ScoreDimensions
    total: float = Field(ge=0, le=100)
    recommendation: Recommendation
    rationale: list[str] = Field(default_factory=list)
    scored_at: datetime = Field(default_factory=utc_now)


class Experiment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    hypothesis: str
    success_metric: str
    success_threshold: str
    max_cost_cents: int = Field(default=0, ge=0)
    max_duration_hours: int = Field(default=24, ge=1)
    requires_external_contact: bool = False
    requires_publication: bool = False


class AuditEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    event_type: str
    entity_id: UUID | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
