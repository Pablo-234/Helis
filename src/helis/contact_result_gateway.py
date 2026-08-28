from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, model_validator

from helis.gtm_domain import LeadResponse, LeadResponseKind, OutreachRun


class ContactResultGatewayConfigurationError(ValueError):
    pass


class ContactResultAck(BaseModel):
    ready: bool = False
    kind: LeadResponseKind | None = None
    summary: str | None = Field(default=None, max_length=2000)
    revenue_cents: int = Field(default=0, ge=0)
    currency: str = Field(default="PLN", min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_ready_result(self) -> ContactResultAck:
        if not self.ready:
            if self.kind is not None or self.revenue_cents != 0:
                raise ValueError("pending contact result cannot contain an outcome or revenue")
            return self
        if self.kind is None or not self.summary or len(self.summary.strip()) < 3:
            raise ValueError("ready contact result requires kind and summary")
        if self.revenue_cents > 0 and self.kind != LeadResponseKind.SALE:
            raise ValueError("revenue is only valid for a sale result")
        return self


class ContactResultGateway(Protocol):
    name: str
    safe_destination: str

    def fetch(self, run: OutreachRun) -> LeadResponse | None: ...


def _validate_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ContactResultGatewayConfigurationError("contact result gateway URL must include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ContactResultGatewayConfigurationError(
            "contact result gateway URL may not contain credentials, query parameters or fragments"
        )
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and allow_insecure_local
        and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise ContactResultGatewayConfigurationError(
        "contact result gateway must use HTTPS; local HTTP requires explicit development opt-in"
    )


@dataclass(slots=True)
class ApprovedContactResultGateway:
    """Read-only operator-owned transport for observed outreach outcomes.

    The external service reports only the observed result for one already-dispatched outreach run.
    HELIS binds venture/lead/run IDs locally, so the gateway cannot redirect revenue to another venture.
    """

    name: ClassVar[str] = "approved_contact_result_gateway_v1"
    url: str
    token: str = ""
    timeout_seconds: int = 30
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedContactResultGateway | None:
        url = os.getenv("HELIS_CONTACT_RESULT_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_CONTACT_RESULT_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_CONTACT_RESULT_GATEWAY_TIMEOUT", "30")),
            allow_insecure_local=(
                os.getenv("HELIS_ALLOW_INSECURE_LOCAL_CONTACT_RESULT_GATEWAY", "0") == "1"
            ),
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def fetch(self, run: OutreachRun) -> LeadResponse | None:
        payload = json.dumps(
            {
                "contract_version": 1,
                "run_id": str(run.id),
                "external_ref": run.external_ref,
                "constraints": {
                    "read_only": True,
                    "one_run_only": True,
                    "observed_results_only": True,
                    "no_inferred_revenue": True,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": str(run.id),
            "X-HELIS-Outreach-Run-ID": str(run.id),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            ack = ContactResultAck.model_validate_json(response.read().decode("utf-8"))
        if not ack.ready:
            return None
        assert ack.kind is not None and ack.summary is not None
        return LeadResponse(
            run_id=run.id,
            lead_id=run.lead_id,
            opportunity_id=run.opportunity_id,
            kind=ack.kind,
            summary=ack.summary,
            revenue_cents=ack.revenue_cents,
            currency=ack.currency.upper(),
        )
