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
from helis.commerce_gateway import CheckoutGatewayAck


@dataclass(slots=True)
class StripeCommerceGateway:
    """Direct Stripe Payment Links adapter with read-only Checkout Session polling."""

    name: ClassVar[str] = "stripe_payment_links_v1"
    api_key: str
    timeout_seconds: int = 30
    api_base: str = "https://api.stripe.com"

    @classmethod
    def from_env(cls) -> StripeCommerceGateway | None:
        api_key = os.getenv("HELIS_STRIPE_SECRET_KEY", "").strip()
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            timeout_seconds=int(os.getenv("HELIS_STRIPE_TIMEOUT", "30")),
        )

    @property
    def safe_destination(self) -> str:
        return "https://api.stripe.com/v1/payment_links"

    def create_checkout(self, run: CheckoutRun, offer: CommerceOffer) -> CheckoutGatewayAck:
        fields: list[tuple[str, str]] = [
            ("line_items[0][price_data][currency]", offer.currency.lower()),
            ("line_items[0][price_data][unit_amount]", str(offer.price_cents)),
            ("line_items[0][price_data][product_data][name]", offer.name),
            ("line_items[0][price_data][product_data][description]", offer.description),
            ("line_items[0][quantity]", "1"),
            ("metadata[helis_offer_id]", str(offer.id)),
            ("metadata[helis_opportunity_id]", str(offer.opportunity_id)),
            ("metadata[helis_offer_hash]", offer.offer_hash),
        ]
        if offer.billing_mode == BillingMode.SUBSCRIPTION:
            fields.append(("line_items[0][price_data][recurring][interval]", self._interval(offer)))
        result = self._request_json(
            "/v1/payment_links",
            method="POST",
            data=urlencode(fields).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Idempotency-Key": str(run.id),
            },
        )
        link_id = str(result.get("id", "")).strip()
        url = str(result.get("url", "")).strip()
        if not link_id or not url:
            raise RuntimeError("Stripe did not return payment link id/url")
        return CheckoutGatewayAck(
            accepted=True,
            external_ref=link_id,
            checkout_url=url,
            metadata={"provider": "stripe", "payment_link_id": link_id},
        )

    def poll_payment(self, binding: CheckoutBinding) -> PaymentGatewayResult | None:
        query = urlencode(
            {
                "payment_link": binding.external_ref,
                "status": "complete",
                "limit": 10,
            }
        )
        result = self._request_json(f"/v1/checkout/sessions?{query}", method="GET")
        sessions = result.get("data")
        if not isinstance(sessions, list):
            return None
        for session in sessions:
            if not isinstance(session, dict):
                continue
            if session.get("payment_status") != "paid":
                continue
            session_id = str(session.get("id", "")).strip()
            currency = str(session.get("currency", "")).strip().upper()
            amount = session.get("amount_total")
            if not session_id or not currency or not isinstance(amount, int) or amount <= 0:
                continue
            return PaymentGatewayResult(
                status=PaymentResultStatus.PAID,
                external_ref=session_id,
                amount_cents=amount,
                currency=currency,
                metadata={"provider": "stripe", "payment_link_id": binding.external_ref},
            )
        return PaymentGatewayResult(status=PaymentResultStatus.PENDING)

    def _request_json(
        self,
        path: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        request_headers = {"Authorization": f"Bearer {self.api_key}"}
        request_headers.update(headers or {})
        request = Request(
            f"{self.api_base}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Stripe returned a non-object response")
        return payload

    @staticmethod
    def _interval(offer: CommerceOffer) -> str:
        unit = offer.pricing_unit.lower()
        if "year" in unit or "annual" in unit:
            return "year"
        if "week" in unit:
            return "week"
        if "day" in unit:
            return "day"
        return "month"
