from __future__ import annotations

from dataclasses import dataclass

from helis.domain import Opportunity
from helis.first_transaction_domain import AcquisitionChannel, PaymentRail
from helis.policy import ActionKind


class UnsafeFirstTransactionPlan(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TransactionExecutionContext:
    prospect_gateway_available: bool = False
    contact_gateway_available: bool = False
    publication_channel_available: bool = False
    payment_gateway_available: bool = False


_ACQUISITION_ACTIONS: dict[AcquisitionChannel, tuple[ActionKind, ...]] = {
    AcquisitionChannel.B2B_DIRECT_OUTREACH: (ActionKind.EXTERNAL_CONTACT,),
    AcquisitionChannel.PARTNERSHIP_OUTREACH: (ActionKind.EXTERNAL_CONTACT,),
    AcquisitionChannel.MARKETPLACE_LISTING: (ActionKind.PUBLICATION,),
    AcquisitionChannel.COMMUNITY_LAUNCH: (ActionKind.PUBLICATION,),
    AcquisitionChannel.CONTENT_INBOUND: (ActionKind.PUBLICATION,),
}

_PAYMENT_ACTIONS: dict[PaymentRail, tuple[ActionKind, ...]] = {
    PaymentRail.MANUAL_INVOICE: (ActionKind.EXTERNAL_CONTACT,),
    PaymentRail.CHECKOUT_LINK: (ActionKind.CREDENTIAL_ACCESS, ActionKind.NETWORK_WRITE),
    PaymentRail.MARKETPLACE_CHECKOUT: (
        ActionKind.CREDENTIAL_ACCESS,
        ActionKind.NETWORK_WRITE,
        ActionKind.PUBLICATION,
    ),
    PaymentRail.PLATFORM_PAYOUT: (ActionKind.CREDENTIAL_ACCESS, ActionKind.NETWORK_WRITE),
}


def required_transaction_actions(
    acquisition_channel: AcquisitionChannel,
    payment_rail: PaymentRail,
) -> list[ActionKind]:
    ordered = [*_ACQUISITION_ACTIONS[acquisition_channel], *_PAYMENT_ACTIONS[payment_rail]]
    return list(dict.fromkeys(ordered))


def transaction_execution_blockers(
    acquisition_channel: AcquisitionChannel,
    payment_rail: PaymentRail,
    context: TransactionExecutionContext,
) -> list[str]:
    blockers: list[str] = []
    if acquisition_channel in {
        AcquisitionChannel.B2B_DIRECT_OUTREACH,
        AcquisitionChannel.PARTNERSHIP_OUTREACH,
    }:
        if not context.prospect_gateway_available:
            blockers.append("prospect_gateway_missing")
        if not context.contact_gateway_available:
            blockers.append("contact_gateway_missing")
    else:
        if not context.publication_channel_available:
            blockers.append("publication_channel_missing")

    if payment_rail == PaymentRail.MANUAL_INVOICE:
        blockers.append("manual_invoice_requires_operator")
    elif not context.payment_gateway_available:
        blockers.append("payment_gateway_missing")
    return list(dict.fromkeys(blockers))


class FirstTransactionPolicy:
    def validate_price(self, opportunity: Opportunity, price_cents: int) -> None:
        business_model = opportunity.business_model
        if business_model is None:
            raise UnsafeFirstTransactionPlan("business model is missing")
        pricing = business_model.pricing
        if pricing.high_cents < 1:
            raise UnsafeFirstTransactionPlan("pricing hypothesis contains no positive paid price")
        lower = max(1, pricing.low_cents)
        if price_cents < lower or price_cents > pricing.high_cents:
            raise UnsafeFirstTransactionPlan(
                "first transaction price must stay inside the persisted pricing hypothesis"
            )

    def validate_route(
        self,
        acquisition_channel: AcquisitionChannel,
        payment_rail: PaymentRail,
    ) -> None:
        if (
            acquisition_channel == AcquisitionChannel.MARKETPLACE_LISTING
            and payment_rail != PaymentRail.MARKETPLACE_CHECKOUT
        ):
            raise UnsafeFirstTransactionPlan(
                "marketplace listing must use marketplace checkout in v1"
            )
        if (
            payment_rail == PaymentRail.MARKETPLACE_CHECKOUT
            and acquisition_channel != AcquisitionChannel.MARKETPLACE_LISTING
        ):
            raise UnsafeFirstTransactionPlan(
                "marketplace checkout requires marketplace listing acquisition in v1"
            )
