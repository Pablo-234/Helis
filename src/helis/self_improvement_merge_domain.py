from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from helis.domain import utc_now


class SelfImprovementMergeStatus(StrEnum):
    WAITING_CI = "waiting_ci"
    WAITING_APPROVAL = "waiting_approval"
    READY = "ready"
    MERGED = "merged"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SelfImprovementCICheck(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    passed: bool


class SelfImprovementCIAttestation(BaseModel):
    candidate_hash: str = Field(min_length=64, max_length=64)
    base_revision: str = Field(min_length=40, max_length=40)
    branch_name: str = Field(min_length=8, max_length=160)
    head_revision: str = Field(min_length=40, max_length=40)
    candidate_file_hashes: dict[str, str] = Field(default_factory=dict)
    passed: bool
    test_count: int = Field(default=0, ge=0)
    checks: list[SelfImprovementCICheck] = Field(default_factory=list, max_length=50)
    attested_at: datetime = Field(default_factory=utc_now)

    @field_validator("base_revision", "head_revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        normalized = value.lower()
        if any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("revision must be a full 40-character hexadecimal commit SHA")
        return normalized


class SelfImprovementMergeRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    branch_run_id: UUID
    proposal_id: UUID
    candidate_id: UUID
    candidate_hash: str = Field(min_length=64, max_length=64)
    base_revision: str = Field(min_length=40, max_length=40)
    branch_name: str = Field(min_length=8, max_length=160)
    status: SelfImprovementMergeStatus = SelfImprovementMergeStatus.WAITING_CI
    approval_granted: bool = False
    ci_attestation: SelfImprovementCIAttestation | None = None
    ci_attestation_hash: str | None = Field(default=None, min_length=64, max_length=64)
    merged_commit_sha: str | None = Field(default=None, min_length=40, max_length=40)
    external_ref: str | None = Field(default=None, max_length=1000)
    destination: str | None = Field(default=None, max_length=1000)
    error: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("base_revision", "merged_commit_sha")
    @classmethod
    def validate_optional_revision(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("revision must be a full 40-character hexadecimal commit SHA")
        return normalized
