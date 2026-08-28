from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from helis.commerce_domain import (
    CheckoutBinding,
    CheckoutRun,
    CommerceOffer,
    PaymentGatewayResult,
)


class CommerceGatewayConfigurationError(ValueError):
    pass


class CheckoutGatewayAck(BaseModel):
    accepted: bool
    external_ref: str = Field(min_length=1, max_length=500)
    checkout_url: str = Field(min_length=8, max_length=2000)
    metadata: dict[str, object] = Field(default_factory=dict)


class PaymentPollResponse(BaseModel):
    result: PaymentGatewayResult | None = None


class CommerceGateway(Protocol):
    name: str
    safe_destination: str

    def create_checkout(self, run: CheckoutRun, offer: CommerceOffer) -> CheckoutGatewayAck: ...

    def poll_payment(self, binding: CheckoutBinding) -> PaymentGatewayResult | None: ...


def _validate_gateway_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise CommerceGatewayConfigurationError("commerce gateway URL must include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CommerceGatewayConfigurationError(
            "commerce gateway URL may not contain credentials, query parameters or fragments"
        )
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and allow_insecure_local
        and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise CommerceGatewayConfigurationError(
        "commerce gateway must use HTTPS; local HTTP requires explicit development opt-in"
    )


def validate_checkout_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise CommerceGatewayConfigurationError("checkout URL returned by gateway must use HTTPS")
    if parsed.username or parsed.password:
        raise CommerceGatewayConfigurationError("checkout URL may not contain embedded credentials")


@dataclass(slots=True)
class ApprovedCommerceGateway:
    """Operator-configured bridge for checkout creation and read-only payment observation."""

    name: ClassVar[str] = "approved_commerce_gateway_v1"
    url: str
    token: str = ""
    timeout_seconds: int = 30
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_gateway_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedCommerceGateway | None:
        url = os.getenv("HELIS_COMMERCE_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_COMMERCE_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_COMMERCE_GATEWAY_TIMEOUT", "30")),
            allow_insecure_local=os.getenv("HELIS_ALLOW_INSECURE_LOCAL_COMMERCE_GATEWAY", "0") == "1",
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def create_checkout(self, run: CheckoutRun, offer: CommerceOffer) -> CheckoutGatewayAck:
        response = self._post(
            {
                "contract_version": 1,
                "operation": "create_checkout",
                "run": run.model_dump(mode="json"),
                "offer": offer.model_dump(mode="json"),
                "constraints": {
                    "exact_price": True,
                    "exact_currency": True,
                    "one_offer_only": True,
                    "no_price_override": True,
                },
            },
            idempotency_key=str(run.id),
        )
        ack = CheckoutGatewayAck.model_validate(response)
        if not ack.accepted:
            raise RuntimeError("commerce gateway rejected checkout creation")
        validate_checkout_url(ack.checkout_url)
        return ack

    def poll_payment(self, binding: CheckoutBinding) -> PaymentGatewayResult | None:
        response = self._post(
            {
                "contract_version": 1,
                "operation": "poll_payment",
                "checkout": {
                    "id": str(binding.id),
                    "external_ref": binding.external_ref,
                    "offer_hash": binding.offer_hash,
                },
            }
        )
        return PaymentPollResponse.model_validate(response).result

    def _post(
        self,
        payload: dict[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        headers = {"Content-Type": "application/json"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
