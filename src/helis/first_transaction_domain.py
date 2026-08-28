from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import utc_now
from helis.policy import ActionKind


class AcquisitionChannel(StrEnum):
    B2B_DIRECT_OUTREACH = "b2b_direct_outreach"
    PARTNERSHIP_OUTREACH = "partnership_outreach"
    MARKETPLACE_LISTING = "marketplace_listing"
    COMMUNITY_LAUNCH = "community_launch"
    CONTENT_INBOUND = "content_inbound"


class PaymentRail(StrEnum):
    MANUAL_INVOICE = "manual_invoice"
    CHECKOUT_LINK = "checkout_link"
    MARKETPLACE_CHECKOUT = "marketplace_checkout"
    PLATFORM_PAYOUT = "platform_payout"


class FirstTransactionPlan(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    architecture_id: UUID
    agent_spec_bundle_id: UUID
    input_hash: str = Field(min_length=64, max_length=64)
    payer: str = Field(min_length=2, max_length=240)
    offer_name: str = Field(min_length=3, max_length=160)
    offer_summary: str = Field(min_length=10, max_length=1200)
    price_cents: int = Field(ge=1, le=100_000_000)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    billing_unit: str = Field(min_length=2, max_length=120)
    acquisition_channel: AcquisitionChannel
    prospect_profile: str = Field(min_length=10, max_length=1200)
    acquisition_strategy: str = Field(min_length=10, max_length=1600)
    required_sales_asset: str = Field(min_length=10, max_length=1600)
    fulfillment_promise: str = Field(min_length=10, max_length=1600)
    fulfillment_steps: list[str] = Field(min_length=1, max_length=8)
    payment_rail: PaymentRail
    first_transaction_success: str = Field(min_length=10, max_length=1200)
    required_actions: list[ActionKind] = Field(default_factory=list, max_length=8)
    execution_blockers: list[str] = Field(default_factory=list, max_length=8)
    owner_responsibilities: list[str] = Field(default_factory=list, max_length=8)
    launch_assumptions: list[str] = Field(default_factory=list, max_length=8)
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def execution_ready(self) -> bool:
        return not self.execution_blockers
