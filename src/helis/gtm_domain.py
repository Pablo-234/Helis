from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import utc_now


class LeadChannel(StrEnum):
    EMAIL = "email"
    WEBFORM = "webform"
    DM = "dm"
    OTHER = "other"


class LeadStage(StrEnum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    DRAFTED = "drafted"
    CONTACTED = "contacted"
    REPLIED = "replied"
    WON = "won"
    LOST = "lost"
    SUPPRESSED = "suppressed"


class ProspectEvidence(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=5, max_length=1200)
    source_url: str | None = Field(default=None, max_length=1500)
    confidence: float = Field(default=0.7, ge=0, le=1)


class ProspectQuery(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    query: str = Field(min_length=3, max_length=500)
    target_customer: str = Field(min_length=2, max_length=500)
    must_have_signals: list[str] = Field(default_factory=list, max_length=8)
    disqualifiers: list[str] = Field(default_factory=list, max_length=8)
    max_results: int = Field(default=10, ge=1, le=25)
    created_at: datetime = Field(default_factory=utc_now)


class Lead(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    organization: str = Field(min_length=2, max_length=300)
    website: str | None = Field(default=None, max_length=1500)
    contact_endpoint: str | None = Field(default=None, max_length=1500)
    channel: LeadChannel = LeadChannel.OTHER
    evidence: list[ProspectEvidence] = Field(min_length=1, max_length=12)
    fit_score: float = Field(default=0, ge=0, le=10)
    fit_rationale: list[str] = Field(default_factory=list, max_length=8)
    stage: LeadStage = LeadStage.DISCOVERED
    created_at: datetime = Field(default_factory=utc_now)


class OutreachDraft(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    opportunity_id: UUID
    channel: LeadChannel
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=20, max_length=4000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=12)
    created_at: datetime = Field(default_factory=utc_now)


class OutreachRunStatus(StrEnum):
    WAITING_APPROVAL = "waiting_approval"
    READY = "ready"
    DISPATCHED = "dispatched"
    WAITING_RESULT = "waiting_result"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class OutreachRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    draft_id: UUID
    lead_id: UUID
    opportunity_id: UUID
    status: OutreachRunStatus = OutreachRunStatus.WAITING_APPROVAL
    approval_granted: bool = False
    external_ref: str | None = None
    destination: str | None = None
    error: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class LeadResponseKind(StrEnum):
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    NO_RESPONSE = "no_response"
    BOUNCE = "bounce"
    MEETING = "meeting"
    SALE = "sale"


class LeadResponse(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    lead_id: UUID
    opportunity_id: UUID
    kind: LeadResponseKind
    summary: str = Field(min_length=3, max_length=2000)
    revenue_cents: int = Field(default=0, ge=0)
    currency: str = Field(default="PLN", min_length=3, max_length=3)
    created_at: datetime = Field(default_factory=utc_now)


class RevenueEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    lead_id: UUID
    response_id: UUID | None = None
    amount_cents: int = Field(gt=0)
    currency: str = Field(default="PLN", min_length=3, max_length=3)
    source: str = Field(min_length=1, max_length=300)
    external_ref: str | None = Field(default=None, max_length=500)
    recorded_at: datetime = Field(default_factory=utc_now)
