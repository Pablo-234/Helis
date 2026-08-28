from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from helis.domain import utc_now


class BranchMaterializationStatus(StrEnum):
    WAITING_APPROVAL = "waiting_approval"
    READY = "ready"
    MATERIALIZED = "materialized"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BranchMaterializationRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    candidate_id: UUID
    evaluation_id: UUID
    candidate_hash: str = Field(min_length=64, max_length=64)
    base_revision: str = Field(min_length=40, max_length=40)
    branch_name: str = Field(min_length=8, max_length=160)
    status: BranchMaterializationStatus = BranchMaterializationStatus.WAITING_APPROVAL
    approval_granted: bool = False
    external_ref: str | None = Field(default=None, max_length=1000)
    destination: str | None = Field(default=None, max_length=1000)
    error: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("base_revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        normalized = value.lower()
        if any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("base revision must be a full 40-character hexadecimal commit SHA")
        return normalized
