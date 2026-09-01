from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from helis.domain import utc_now


class OperatorRequestKind(StrEnum):
    VALIDATION = "validation"
    PREVIEW_PUBLICATION = "preview_publication"
    COMMERCE_CHECKOUT = "commerce_checkout"
    OUTREACH = "outreach"
    SELF_BRANCH = "self_branch"
    SELF_MERGE = "self_merge"
    CAPABILITY_RESULT = "capability_result"


class OperatorRequestType(StrEnum):
    APPROVAL = "approval"
    INPUT = "input"


class OperatorDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class OperatorInboxItem(BaseModel):
    key: str = Field(min_length=3, max_length=260)
    kind: OperatorRequestKind
    request_type: OperatorRequestType
    run_id: UUID
    opportunity_id: UUID | None = None
    capability_key: str | None = Field(default=None, max_length=60)
    venture_title: str = Field(default="system", min_length=1, max_length=240)
    title: str = Field(min_length=3, max_length=240)
    summary: str = Field(min_length=3, max_length=2000)
    consequence: str = Field(min_length=3, max_length=2000)
    binding: str = Field(min_length=1, max_length=1000)
    confirmation_token: str | None = Field(
        default=None,
        min_length=16,
        max_length=16,
        pattern=r"^[0-9a-f]{16}$",
    )
    action_command: str = Field(min_length=3, max_length=2000)
    details: dict[str, str] = Field(default_factory=dict)
    priority: int = Field(default=50, ge=0, le=100)
    updated_at: datetime = Field(default_factory=utc_now)


class OperatorDecisionReceipt(BaseModel):
    key: str
    decision: OperatorDecision
    run_id: UUID
    kind: OperatorRequestKind
    confirmation_token: str
    resulting_status: str = Field(min_length=2, max_length=80)
    reason: str | None = Field(default=None, max_length=1000)
    decided_at: datetime = Field(default_factory=utc_now)
