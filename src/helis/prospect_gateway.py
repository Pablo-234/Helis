from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from helis.gtm_domain import LeadChannel, ProspectEvidence, ProspectQuery


class ProspectGatewayConfigurationError(ValueError):
    pass


class ProspectCandidate(BaseModel):
    organization: str = Field(min_length=2, max_length=300)
    website: str | None = Field(default=None, max_length=1500)
    contact_endpoint: str | None = Field(default=None, max_length=1500)
    channel: LeadChannel = LeadChannel.OTHER
    evidence: list[ProspectEvidence] = Field(min_length=1, max_length=12)


class ProspectGatewayResponse(BaseModel):
    candidates: list[ProspectCandidate] = Field(default_factory=list, max_length=25)


class ProspectGateway(Protocol):
    name: str
    safe_destination: str

    def search(self, query: ProspectQuery) -> list[ProspectCandidate]: ...


def _validate_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ProspectGatewayConfigurationError("prospect gateway URL must include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProspectGatewayConfigurationError(
            "prospect gateway URL may not contain credentials, query parameters or fragments"
        )
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and allow_insecure_local
        and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise ProspectGatewayConfigurationError(
        "prospect gateway must use HTTPS; local HTTP requires explicit development opt-in"
    )


@dataclass(slots=True)
class ApprovedProspectGateway:
    """Read-only market search bridge. Search destination is operator configured."""

    name: ClassVar[str] = "approved_prospect_gateway_v1"
    url: str
    token: str = ""
    timeout_seconds: int = 30
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedProspectGateway | None:
        url = os.getenv("HELIS_PROSPECT_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_PROSPECT_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_PROSPECT_GATEWAY_TIMEOUT", "30")),
            allow_insecure_local=os.getenv("HELIS_ALLOW_INSECURE_LOCAL_PROSPECT_GATEWAY", "0") == "1",
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def search(self, query: ProspectQuery) -> list[ProspectCandidate]:
        payload = json.dumps(
            {"contract_version": 1, "query": query.model_dump(mode="json")},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-HELIS-Query-ID": str(query.id)}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            result = ProspectGatewayResponse.model_validate_json(response.read().decode("utf-8"))
        return result.candidates[: query.max_results]
