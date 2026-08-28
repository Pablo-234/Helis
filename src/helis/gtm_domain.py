from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

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


class LeadContactOption(BaseModel):
    channel: LeadChannel
    endpoint: str = Field(min_length=3, max_length=1500)


class Lead(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    organization: str = Field(min_length=2, max_length=300)
    website: str | None = Field(default=None, max_length=1500)
    contact_endpoint: str | None = Field(default=None, max_length=1500)
    channel: LeadChannel = LeadChannel.OTHER
    contact_options: list[LeadContactOption] = Field(default_factory=list, max_length=8)
    evidence: list[ProspectEvidence] = Field(min_length=1, max_length=12)
    fit_score: float = Field(default=0, ge=0, le=10)
    fit_rationale: list[str] = Field(default_factory=list, max_length=8)
    stage: LeadStage = LeadStage.DISCOVERED
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_contact_options(self) -> Lead:
        seen: set[tuple[LeadChannel, str]] = set()
        for option in self.contact_options:
            key = (option.channel, option.endpoint.strip())
            if key in seen:
                raise ValueError("lead contact_options must be unique")
            seen.add(key)
        return self


def lead_contact_options(lead: Lead) -> list[LeadContactOption]:
    """Return the legacy primary endpoint plus explicit alternatives, de-duplicated in order."""
    options: list[LeadContactOption] = []
    seen: set[tuple[LeadChannel, str]] = set()
    if lead.contact_endpoint:
        primary = LeadContactOption(channel=lead.channel, endpoint=lead.contact_endpoint)
        options.append(primary)
        seen.add((primary.channel, primary.endpoint.strip()))
    for option in lead.contact_options:
        key = (option.channel, option.endpoint.strip())
        if key in seen:
            continue
        options.append(option)
        seen.add(key)
    return options


class OutreachDraft(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    opportunity_id: UUID
    channel: LeadChannel
    contact_endpoint: str | None = Field(default=None, max_length=1500)
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=20, max_length=4000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=12)
    experiment_id: UUID | None = None
    experiment_arm_key: str | None = Field(default=None, max_length=31)
    channel_experiment_id: UUID | None = None
    channel_experiment_arm_key: str | None = Field(default=None, max_length=31)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_experiment_binding(self) -> OutreachDraft:
        if (self.experiment_id is None) != (self.experiment_arm_key is None):
            raise ValueError("experiment_id and experiment_arm_key must be set together")
        if (self.channel_experiment_id is None) != (self.channel_experiment_arm_key is None):
            raise ValueError(
                "channel_experiment_id and channel_experiment_arm_key must be set together"
            )
        if self.channel_experiment_id is not None and not self.contact_endpoint:
            raise ValueError("channel experiment drafts require an explicit contact_endpoint")
        return self


class OutreachRunStatus(StrEnum):
    WAITING_APPROVAL = "waiting_approval"
    READY = "ready"
    DISPATCHED = "dispatched"
    WAITING_RESULT = "waiting_result"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OutreachRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    draft_id: UUID
    lead_id: UUID
    opportunity_id: UUID
    draft_hash: str = Field(min_length=64, max_length=64)
    status: OutreachRunStatus = OutreachRunStatus.WAITING_APPROVAL
    approval_granted: bool = False
    external_ref: str | None = None
    destination: str | None = None
    error: str | None = None
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None
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
