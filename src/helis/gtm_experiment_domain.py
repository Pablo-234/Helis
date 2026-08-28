from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from helis.domain import utc_now


class GTMExperimentKind(StrEnum):
    OFFER = "offer"
    PRICING = "pricing"


class GTMExperimentStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class GTMExperimentArm(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,30}$")
    label: str = Field(min_length=2, max_length=100)
    offer_summary: str = Field(min_length=10, max_length=800)
    price_cents: int | None = Field(default=None, ge=100, le=10_000_000)
    currency: str = Field(default="PLN", min_length=3, max_length=3)


class GTMExperiment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    kind: GTMExperimentKind
    hypothesis: str = Field(min_length=10, max_length=1200)
    arms: list[GTMExperimentArm] = Field(min_length=2, max_length=2)
    minimum_resolved_per_arm: int = Field(default=2, ge=2, le=10)
    max_resolved_per_arm: int = Field(default=5, ge=2, le=20)
    minimum_lift: float = Field(default=0.20, gt=0, le=1)
    status: GTMExperimentStatus = GTMExperimentStatus.ACTIVE
    winner_arm_key: str | None = None
    conclusion: str | None = Field(default=None, max_length=1200)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_experiment(self) -> GTMExperiment:
        keys = [arm.key for arm in self.arms]
        if set(keys) != {"control", "variant"}:
            raise ValueError("GTM experiments require exactly control and variant arms")
        if self.max_resolved_per_arm < self.minimum_resolved_per_arm:
            raise ValueError("max_resolved_per_arm must be >= minimum_resolved_per_arm")
        if self.kind == GTMExperimentKind.PRICING:
            prices = [arm.price_cents for arm in self.arms]
            if any(price is None for price in prices):
                raise ValueError("pricing experiments require an explicit price on both arms")
            currencies = {arm.currency.upper() for arm in self.arms}
            if len(currencies) != 1:
                raise ValueError("pricing experiment arms must use the same currency")
            numeric_prices = [int(price) for price in prices if price is not None]
            if max(numeric_prices) > min(numeric_prices) * 4:
                raise ValueError("pricing experiment arms may differ by at most 4x")
        if self.winner_arm_key is not None and self.winner_arm_key not in set(keys):
            raise ValueError("winner_arm_key must reference an experiment arm")
        return self


class GTMArmMetrics(BaseModel):
    arm_key: str
    resolved: int = Field(default=0, ge=0)
    sales: int = Field(default=0, ge=0)
    meetings: int = Field(default=0, ge=0)
    interested: int = Field(default=0, ge=0)
    revenue_cents: int = Field(default=0, ge=0)
    outcome_score: float = Field(default=0, ge=0, le=1)


class GTMExperimentSnapshot(BaseModel):
    experiment_id: UUID
    arms: list[GTMArmMetrics]
    completed: bool = False
    winner_arm_key: str | None = None
    conclusion: str = Field(min_length=3, max_length=1200)
