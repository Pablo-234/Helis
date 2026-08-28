from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from helis.domain import Experiment, ExperimentRun, ExternalDispatch, Opportunity


class GatewayConfigurationError(ValueError):
    pass


class GatewayAck(BaseModel):
    accepted: bool
    dispatch_id: str = Field(min_length=1, max_length=300)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


def _validate_gateway_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise GatewayConfigurationError("validation gateway URL must include a host")
    if parsed.username or parsed.password:
        raise GatewayConfigurationError("credentials are not allowed inside the gateway URL")
    if parsed.query or parsed.fragment:
        raise GatewayConfigurationError("gateway URL must not include query parameters or fragments")

    hostname = parsed.hostname.lower()
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and allow_insecure_local and hostname in local_hosts:
        return
    raise GatewayConfigurationError(
        "validation gateway must use HTTPS; HTTP is allowed only for explicitly enabled localhost dev"
    )


@dataclass(slots=True)
class ApprovedValidationGateway:
    """External side-effect bridge. Destination is operator-configured, never model-selected."""

    name: ClassVar[str] = "approved_validation_gateway_v1"
    requires_run_approval: ClassVar[bool] = True
    requires_cash_reservation: ClassVar[bool] = True

    url: str
    token: str = ""
    timeout_seconds: int = 30
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_gateway_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedValidationGateway | None:
        url = os.getenv("HELIS_VALIDATION_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_VALIDATION_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_VALIDATION_GATEWAY_TIMEOUT", "30")),
            allow_insecure_local=os.getenv("HELIS_ALLOW_INSECURE_LOCAL_GATEWAY", "0") == "1",
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def execute(
        self,
        experiment: Experiment,
        opportunity: Opportunity,
        run: ExperimentRun,
    ) -> ExternalDispatch:
        payload = json.dumps(
            {
                "contract_version": 1,
                "run": run.model_dump(mode="json"),
                "experiment": experiment.model_dump(mode="json"),
                "opportunity": opportunity.model_dump(mode="json"),
                "constraints": {
                    "max_cost_cents": experiment.max_cost_cents,
                    "max_duration_hours": experiment.max_duration_hours,
                    "one_run_only": True,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": str(run.id),
            "X-HELIS-Run-ID": str(run.id),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            ack = GatewayAck.model_validate_json(response.read().decode("utf-8"))
        if not ack.accepted:
            raise RuntimeError("validation gateway rejected the dispatch")
        return ExternalDispatch(
            dispatch_id=ack.dispatch_id,
            channel=self.name,
            metadata=dict(ack.metadata),
        )
