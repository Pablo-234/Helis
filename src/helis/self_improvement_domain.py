from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import utc_now


class ImprovementStatus(StrEnum):
    PROPOSED = "proposed"
    MATERIALIZED = "materialized"
    WAITING_EVALUATION = "waiting_evaluation"
    WAITING_MERGE_APPROVAL = "waiting_merge_approval"
    REJECTED = "rejected"


class ImprovementRisk(StrEnum):
    LOW = "low"


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"


class ImprovementSignal(BaseModel):
    event_id: UUID
    event_type: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=3, max_length=1200)
    created_at: datetime


class SelfImprovementProposal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    objective: str = Field(min_length=10, max_length=1200)
    rationale: list[str] = Field(min_length=1, max_length=8)
    signal_ids: list[UUID] = Field(default_factory=list, max_length=20)
    target_files: list[str] = Field(min_length=1, max_length=2)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)
    metric_name: str = Field(min_length=2, max_length=120)
    metric_direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER
    minimum_improvement: float = Field(default=0.01, gt=0, le=1000)
    risk: ImprovementRisk = ImprovementRisk.LOW
    status: ImprovementStatus = ImprovementStatus.PROPOSED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CandidateFile(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    original_sha256: str = Field(min_length=64, max_length=64)
    content: str = Field(min_length=1, max_length=40_000)


class SelfImprovementCandidate(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    files: list[CandidateFile] = Field(min_length=1, max_length=2)
    candidate_hash: str = Field(min_length=64, max_length=64)
    workspace: str = Field(min_length=1, max_length=1500)
    created_at: datetime = Field(default_factory=utc_now)


class EvaluationSnapshot(BaseModel):
    passed: bool
    test_count: int = Field(default=0, ge=0)
    metric_value: float
    checks: list[str] = Field(default_factory=list, max_length=30)


class SelfImprovementEvaluation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    candidate_id: UUID
    candidate_hash: str = Field(min_length=64, max_length=64)
    metric_name: str = Field(min_length=2, max_length=120)
    metric_direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER
    baseline: EvaluationSnapshot
    candidate: EvaluationSnapshot
    regressions: list[str] = Field(default_factory=list, max_length=30)
    accepted: bool = False
    reason: str = Field(min_length=3, max_length=1200)
    created_at: datetime = Field(default_factory=utc_now)
