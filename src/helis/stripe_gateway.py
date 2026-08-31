from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from helis.commerce_domain import (
    BillingMode,
    CheckoutBinding,
    CheckoutRun,
    CommerceOffer,
    PaymentGatewayResult,
    PaymentResultStatus,
)
from helis.commerce_gateway import CheckoutGatewayAck, validate_checkout_url


class StripeGatewayConfigurationError(ValueError):
    pass


def _subscription_interval(unit: str) -> str:
    lowered = unit.lower()
    if any(token in lowered for token in ("year", "annual", "rok", "rocznie")):
        return "year"
    if any(token in lowered for token in ("week", "tydzie", "weekly")):
        return "week"
    if any(token in lowered for token in ("day", "dzień", "dzien", "daily")):
        return "day"
    return "month"


@dataclass(slots=True)
class StripeCommerceGateway:
    """Direct Stripe Payment Links adapter with read-only Checkout Session polling."""

    name: ClassVar[str] = "stripe_payment_links_v1"
    secret_key: str
    timeout_seconds: int = 30
    api_base: str = "https://api.stripe.com"

    def __post_init__(self) -> None:
        if not self.secret_key.strip():
            raise StripeGatewayConfigurationError("Stripe secret key is empty")
        if not self.api_base.startswith("https://"):
            raise StripeGatewayConfigurationError("Stripe API base must use HTTPS")

    @classmethod
    def from_env(cls) -> StripeCommerceGateway | None:
        key = (
            os.getenv("HELIS_STRIPE_SECRET_KEY", "").strip()
            or os.getenv("STRIPE_SECRET_KEY", "").strip()
        )
        if not key:
            return None
        return cls(
            secret_key=key,
            timeout_seconds=int(os.getenv("HELIS_STRIPE_TIMEOUT", "30")),
        )

    @property
    def safe_destination(self) -> str:
        return "https://api.stripe.com/v1/payment_links"

    def create_checkout(self, run: CheckoutRun, offer: CommerceOffer) -> CheckoutGatewayAck:
        fields: list[tuple[str, str]] = [
            ("line_items[0][price_data][currency]", offer.currency.lower()),
            ("line_items[0][price_data][unit_amount]", str(offer.price_cents)),
            ("line_items[0][price_data][product_data][name]", offer.name[:250]),
            ("line_items[0][price_data][product_data][description]", offer.description[:500]),
            ("line_items[0][quantity]", "1"),
            ("metadata[helis_offer_id]", str(offer.id)),
            ("metadata[helis_offer_hash]", offer.offer_hash),
            ("metadata[helis_checkout_run_id]", str(run.id)),
        ]
        if offer.billing_mode == BillingMode.SUBSCRIPTION:
            fields.append(
                (
                    "line_items[0][price_data][recurring][interval]",
                    _subscription_interval(offer.pricing_unit),
                )
            )
        payload = urlencode(fields).encode("utf-8")
        request = Request(
            f"{self.api_base}/v1/payment_links",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Idempotency-Key": str(run.id),
                "User-Agent": "HELIS/0.1 commerce",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        link_id = str(data.get("id") or "").strip()
        checkout_url = str(data.get("url") or "").strip()
        if not link_id or not checkout_url or data.get("active") is False:
            raise RuntimeError("Stripe did not return an active Payment Link")
        validate_checkout_url(checkout_url)
        return CheckoutGatewayAck(
            accepted=True,
            external_ref=link_id,
            checkout_url=checkout_url,
            metadata={
                "provider": "stripe",
                "livemode": bool(data.get("livemode", False)),
                "billing_mode": offer.billing_mode.value,
            },
        )

    def poll_payment(self, binding: CheckoutBinding) -> PaymentGatewayResult | None:
        params = urlencode({"payment_link": binding.external_ref, "limit": 20})
        request = Request(
            f"{self.api_base}/v1/checkout/sessions?{params}",
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Accept": "application/json",
                "User-Agent": "HELIS/0.1 commerce",
            },
            method="GET",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        sessions = payload.get("data", [])
        if not isinstance(sessions, list):
            raise TypeError("Stripe Checkout Sessions response has invalid data shape")
        for session in sessions:
            if not isinstance(session, dict):
                continue
            if str(session.get("payment_link") or "") != binding.external_ref:
                continue
            if str(session.get("payment_status") or "").lower() != "paid":
                continue
            session_id = str(session.get("id") or "").strip()
            amount = session.get("amount_total")
            currency = str(session.get("currency") or "").upper()
            if not session_id or not isinstance(amount, int) or amount <= 0 or len(currency) != 3:
                continue
            return PaymentGatewayResult(
                status=PaymentResultStatus.PAID,
                external_ref=session_id,
                amount_cents=amount,
                currency=currency,
                metadata={
                    "provider": "stripe",
                    "payment_link": binding.external_ref,
                    "mode": session.get("mode"),
                    "livemode": bool(session.get("livemode", False)),
                },
            )
        return PaymentGatewayResult(status=PaymentResultStatus.PENDING)
