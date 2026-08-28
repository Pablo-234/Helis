from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import utc_now


class PreviewPublishStatus(StrEnum):
    WAITING_APPROVAL = "waiting_approval"
    READY = "ready"
    PUBLISHED = "published"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PreviewPublishRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    preview_id: UUID
    opportunity_id: UUID
    artifact_hash: str = Field(min_length=64, max_length=64)
    status: PreviewPublishStatus = PreviewPublishStatus.WAITING_APPROVAL
    approval_granted: bool = False
    destination: str | None = None
    external_ref: str | None = None
    error: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class PublishedPreview(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    preview_id: UUID
    opportunity_id: UUID
    artifact_hash: str = Field(min_length=64, max_length=64)
    preview_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    published_at: datetime = Field(default_factory=utc_now)
