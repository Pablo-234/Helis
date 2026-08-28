from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from helis.domain import DeliveryModel, RevenueModel, utc_now


class BillingMode(StrEnum):
    ONE_TIME = "one_time"
    SUBSCRIPTION = "subscription"


class CheckoutRunStatus(StrEnum):
    WAITING_APPROVAL = "waiting_approval"
    READY = "ready"
    ACTIVE = "active"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentResultStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"


class CommerceOffer(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    offer_hash: str = Field(min_length=64, max_length=64)
    name: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=1600)
    price_cents: int = Field(gt=0, le=100_000_000)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    pricing_unit: str = Field(min_length=2, max_length=120)
    billing_mode: BillingMode
    revenue_model: RevenueModel
    delivery_model: DeliveryModel
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def display_price(self) -> str:
        whole, cents = divmod(self.price_cents, 100)
        return f"{whole}.{cents:02d} {self.currency}"


class CheckoutRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    offer_id: UUID
    opportunity_id: UUID
    offer_hash: str = Field(min_length=64, max_length=64)
    status: CheckoutRunStatus = CheckoutRunStatus.WAITING_APPROVAL
    approval_granted: bool = False
    destination: str | None = None
    external_ref: str | None = None
    error: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class CheckoutBinding(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    offer_id: UUID
    opportunity_id: UUID
    offer_hash: str = Field(min_length=64, max_length=64)
    checkout_url: str = Field(min_length=8, max_length=2000)
    external_ref: str = Field(min_length=1, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class PaymentGatewayResult(BaseModel):
    status: PaymentResultStatus
    external_ref: str | None = Field(default=None, max_length=500)
    amount_cents: int = Field(default=0, ge=0, le=100_000_000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payment_shape(self) -> PaymentGatewayResult:
        if self.status == PaymentResultStatus.PENDING:
            if self.external_ref is not None or self.amount_cents != 0 or self.currency is not None:
                raise ValueError("pending payment result cannot claim payment data")
            return self
        if not self.external_ref:
            raise ValueError("paid payment result requires external_ref")
        if self.amount_cents <= 0:
            raise ValueError("paid payment result requires a positive amount")
        if self.currency is None:
            raise ValueError("paid payment result requires currency")
        self.currency = self.currency.upper()
        return self


class CommerceRevenueEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    offer_id: UUID
    checkout_id: UUID
    amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    source: str = Field(default="self_serve_checkout", min_length=2, max_length=200)
    external_ref: str = Field(min_length=1, max_length=500)
    recorded_at: datetime = Field(default_factory=utc_now)


class CommerceBuildContext(BaseModel):
    offer_id: UUID
    offer_hash: str = Field(min_length=64, max_length=64)
    checkout_url: str = Field(min_length=8, max_length=2000)
    price_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    display_price: str = Field(min_length=5, max_length=80)
    billing_mode: BillingMode
