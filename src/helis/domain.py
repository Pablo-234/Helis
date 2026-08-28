from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class VentureStage(StrEnum):
    DISCOVERED = "discovered"
    EVALUATED = "evaluated"
    VALIDATING = "validating"
    VALIDATED = "validated"
    BUILDING = "building"
    READY_PREVIEW = "ready_preview"
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


class Observation(BaseModel):
    """Raw external signal. This is evidence input, not a model inference."""

    id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=3)
    source: str = Field(min_length=1)
    captured_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    observation_id: UUID | None = None
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


class Assumption(BaseModel):
    statement: str = Field(min_length=5)
    failure_mode: str = Field(min_length=5)
    falsifier: str = Field(min_length=5)
    criticality: float = Field(ge=0, le=10)
    uncertainty: float = Field(ge=0, le=10)

    @property
    def risk(self) -> float:
        return round(self.criticality * self.uncertainty / 10, 2)


class SkepticReport(BaseModel):
    opportunity_id: UUID
    assumptions: list[Assumption] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def max_assumption_risk(self) -> float:
        return max((assumption.risk for assumption in self.assumptions), default=0.0)


class ExperimentType(StrEnum):
    DESK_RESEARCH = "desk_research"
    INTERVIEW = "interview"
    SMOKE_TEST = "smoke_test"
    PRICING = "pricing"
    CONCIERGE = "concierge"
    PROTOTYPE = "prototype"
    SALES = "sales"
    OTHER = "other"


class Experiment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    title: str = Field(min_length=3)
    experiment_type: ExperimentType
    hypothesis: str = Field(min_length=5)
    success_metric: str = Field(min_length=3)
    success_threshold: str = Field(min_length=2)
    targeted_assumptions: list[int] = Field(default_factory=list)
    expected_information_gain: float = Field(default=5, ge=0, le=10)
    effort_score: float = Field(default=5, ge=0, le=10)
    max_cost_cents: int = Field(default=0, ge=0)
    max_duration_hours: int = Field(default=24, ge=1)
    requires_external_contact: bool = False
    requires_publication: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class ExperimentRunStatus(StrEnum):
    PLANNED = "planned"
    WAITING_APPROVAL = "waiting_approval"
    READY = "ready"
    RUNNING = "running"
    WAITING_RESULT = "waiting_result"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ExperimentRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    experiment_id: UUID
    opportunity_id: UUID
    status: ExperimentRunStatus = ExperimentRunStatus.PLANNED
    adapter: str | None = None
    approval_granted: bool = False
    external_ref: str | None = None
    attempt: int = Field(default=1, ge=1)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    actual_cost_cents: float = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)


class ExternalDispatch(BaseModel):
    dispatch_id: str = Field(min_length=1, max_length=300)
    channel: str = Field(default="validation_gateway", min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    accepted_at: datetime = Field(default_factory=utc_now)


class ValidationOutcome(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    INCONCLUSIVE = "inconclusive"


class ValidationResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    experiment_id: UUID
    opportunity_id: UUID
    outcome: ValidationOutcome
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=3)
    supporting_observation_ids: list[UUID] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    pivot_signal: str | None = None
    source: str = Field(default="unknown", min_length=1)
    actual_cost_cents: float = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class VentureDecisionKind(StrEnum):
    ADVANCE = "advance"
    CONTINUE = "continue"
    PIVOT = "pivot"
    KILL = "kill"


class VentureDecision(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    decision: VentureDecisionKind
    confidence: float = Field(ge=0, le=1)
    rationale: list[str] = Field(default_factory=list)
    result_ids: list[UUID] = Field(default_factory=list)
    suggested_pivot: str | None = None
    decided_at: datetime = Field(default_factory=utc_now)


class BuildTemplate(StrEnum):
    STATIC_WEB = "static_web_v1"
    CONCIERGE_OPS = "concierge_ops_v1"


class BuildStatus(StrEnum):
    PLANNED = "planned"
    VERIFIED = "verified"
    READY_PREVIEW = "ready_preview"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class BuildSpec(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    template: BuildTemplate
    name: str = Field(min_length=3, max_length=120)
    goal: str = Field(min_length=10, max_length=1200)
    acceptance_criteria: list[str] = Field(min_length=2, max_length=8)
    max_files: int = Field(default=6, ge=1, le=20)
    max_total_bytes: int = Field(default=80_000, ge=1, le=1_000_000)
    created_at: datetime = Field(default_factory=utc_now)


class BuildFile(BaseModel):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=250_000)


class BuildBundle(BaseModel):
    files: list[BuildFile] = Field(min_length=1, max_length=20)


class BuildRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    spec_id: UUID
    opportunity_id: UUID
    status: BuildStatus = BuildStatus.PLANNED
    workspace: str | None = None
    file_paths: list[str] = Field(default_factory=list)
    attempt: int = Field(default=1, ge=1)
    error: str | None = None
    completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class BuildCheck(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID | None = None
    name: str = Field(min_length=2, max_length=120)
    passed: bool
    details: str = Field(min_length=1, max_length=2000)
    created_at: datetime = Field(default_factory=utc_now)


class BuildReviewVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class BuildReview(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    verdict: BuildReviewVerdict
    score: float = Field(ge=0, le=10)
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=3, max_length=2000)
    created_at: datetime = Field(default_factory=utc_now)


class PreviewManifest(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    opportunity_id: UUID
    workspace: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1, max_length=240)
    artifact_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    event_type: str
    entity_id: UUID | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
