from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from helis.domain import BuildBundle, PreviewManifest
from helis.preview_domain import PreviewPublishRun


class PreviewGatewayConfigurationError(ValueError):
    pass


class PreviewGatewayAck(BaseModel):
    accepted: bool
    dispatch_id: str = Field(min_length=1, max_length=300)
    preview_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreviewGateway(Protocol):
    name: str
    safe_destination: str

    def execute(
        self,
        run: PreviewPublishRun,
        preview: PreviewManifest,
        bundle: BuildBundle,
    ) -> PreviewGatewayAck: ...


def _validate_gateway_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise PreviewGatewayConfigurationError("preview gateway URL must include a host")
    if parsed.username or parsed.password:
        raise PreviewGatewayConfigurationError("credentials are not allowed inside the gateway URL")
    if parsed.query or parsed.fragment:
        raise PreviewGatewayConfigurationError(
            "preview gateway URL must not include query parameters or fragments"
        )
    hostname = parsed.hostname.lower()
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and allow_insecure_local
        and hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise PreviewGatewayConfigurationError(
        "preview gateway must use HTTPS; HTTP is allowed only for explicitly enabled localhost dev"
    )


@dataclass(slots=True)
class ApprovedPreviewGateway:
    """Publishes only an already-reviewed artifact to an operator-configured destination."""

    name: ClassVar[str] = "approved_preview_gateway_v1"

    url: str
    token: str = ""
    timeout_seconds: int = 30
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_gateway_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedPreviewGateway | None:
        url = os.getenv("HELIS_PREVIEW_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_PREVIEW_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_PREVIEW_GATEWAY_TIMEOUT", "30")),
            allow_insecure_local=os.getenv("HELIS_ALLOW_INSECURE_LOCAL_PREVIEW_GATEWAY", "0") == "1",
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def execute(
        self,
        run: PreviewPublishRun,
        preview: PreviewManifest,
        bundle: BuildBundle,
    ) -> PreviewGatewayAck:
        payload = json.dumps(
            {
                "contract_version": 1,
                "publish_run": run.model_dump(mode="json"),
                "preview": preview.model_dump(mode="json"),
                "artifact": {
                    "sha256": preview.artifact_hash,
                    "entrypoint": preview.entrypoint,
                    "files": [item.model_dump(mode="json") for item in bundle.files],
                },
                "constraints": {
                    "immutable_reviewed_hash": True,
                    "one_run_only": True,
                    "production": False,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": str(run.id),
            "X-HELIS-Publish-Run-ID": str(run.id),
            "X-HELIS-Artifact-SHA256": preview.artifact_hash,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            ack = PreviewGatewayAck.model_validate_json(response.read().decode("utf-8"))
        if not ack.accepted:
            raise RuntimeError("preview gateway rejected the publication")
        return ack
