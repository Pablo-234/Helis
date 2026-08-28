from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from helis.gtm_domain import Lead, OutreachDraft, OutreachRun


class ContactGatewayConfigurationError(ValueError):
    pass


class ContactGatewayAck(BaseModel):
    accepted: bool
    dispatch_id: str = Field(min_length=1, max_length=300)
    channel: str = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContactGateway(Protocol):
    name: str
    safe_destination: str

    def send(self, run: OutreachRun, lead: Lead, draft: OutreachDraft) -> ContactGatewayAck: ...


def _validate_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ContactGatewayConfigurationError("contact gateway URL must include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ContactGatewayConfigurationError(
            "contact gateway URL may not contain credentials, query parameters or fragments"
        )
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and allow_insecure_local
        and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise ContactGatewayConfigurationError(
        "contact gateway must use HTTPS; local HTTP requires explicit development opt-in"
    )


@dataclass(slots=True)
class ApprovedContactGateway:
    """Operator-configured transport for one already-approved B2B outreach run."""

    name: ClassVar[str] = "approved_contact_gateway_v1"
    url: str
    token: str = ""
    timeout_seconds: int = 30
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedContactGateway | None:
        url = os.getenv("HELIS_CONTACT_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_CONTACT_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_CONTACT_GATEWAY_TIMEOUT", "30")),
            allow_insecure_local=os.getenv("HELIS_ALLOW_INSECURE_LOCAL_CONTACT_GATEWAY", "0") == "1",
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def send(self, run: OutreachRun, lead: Lead, draft: OutreachDraft) -> ContactGatewayAck:
        payload = json.dumps(
            {
                "contract_version": 1,
                "run": run.model_dump(mode="json"),
                "lead": lead.model_dump(mode="json"),
                "draft": draft.model_dump(mode="json"),
                "constraints": {
                    "first_contact_only": True,
                    "no_automatic_followup": True,
                    "honor_opt_out": True,
                    "one_run_only": True,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": str(run.id),
            "X-HELIS-Outreach-Run-ID": str(run.id),
            "X-HELIS-Draft-SHA256": run.draft_hash,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            ack = ContactGatewayAck.model_validate_json(response.read().decode("utf-8"))
        if not ack.accepted:
            raise RuntimeError("contact gateway rejected outreach")
        return ack
