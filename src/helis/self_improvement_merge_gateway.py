from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from helis.self_improvement_branch_domain import BranchMaterializationRun
from helis.self_improvement_merge_domain import (
    SelfImprovementCIAttestation,
    SelfImprovementMergeRun,
)


class SelfImprovementMergeGatewayConfigurationError(ValueError):
    pass


class SelfImprovementMergeAck(BaseModel):
    candidate_hash: str = Field(min_length=64, max_length=64)
    base_revision: str = Field(min_length=40, max_length=40)
    branch_name: str = Field(min_length=8, max_length=160)
    head_revision: str = Field(min_length=40, max_length=40)
    default_branch_before: str = Field(min_length=40, max_length=40)
    merged_commit_sha: str = Field(min_length=40, max_length=40)
    external_ref: str = Field(min_length=1, max_length=1000)


class SelfImprovementMergeGateway(Protocol):
    name: str
    safe_destination: str

    def merge(
        self,
        run: SelfImprovementMergeRun,
        branch_run: BranchMaterializationRun,
        attestation: SelfImprovementCIAttestation,
    ) -> SelfImprovementMergeAck: ...


def _validate_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise SelfImprovementMergeGatewayConfigurationError("self-merge gateway URL must include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SelfImprovementMergeGatewayConfigurationError(
            "self-merge gateway URL may not contain credentials, query parameters or fragments"
        )
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and allow_insecure_local
        and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise SelfImprovementMergeGatewayConfigurationError(
        "self-merge gateway must use HTTPS; local HTTP requires explicit development opt-in"
    )


@dataclass(slots=True)
class ApprovedSelfImprovementMergeGateway:
    """Final operator-owned merge boundary; refuses stale base/default-branch state."""

    name: ClassVar[str] = "approved_self_improvement_merge_gateway_v1"
    url: str
    token: str = ""
    timeout_seconds: int = 120
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedSelfImprovementMergeGateway | None:
        url = os.getenv("HELIS_SELF_MERGE_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_SELF_MERGE_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_SELF_MERGE_GATEWAY_TIMEOUT", "120")),
            allow_insecure_local=os.getenv("HELIS_ALLOW_INSECURE_LOCAL_SELF_MERGE_GATEWAY", "0")
            == "1",
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def merge(
        self,
        run: SelfImprovementMergeRun,
        branch_run: BranchMaterializationRun,
        attestation: SelfImprovementCIAttestation,
    ) -> SelfImprovementMergeAck:
        payload = json.dumps(
            {
                "contract_version": 1,
                "run": run.model_dump(mode="json"),
                "branch_run": branch_run.model_dump(mode="json"),
                "ci_attestation": attestation.model_dump(mode="json"),
                "constraints": {
                    "exact_candidate_hash": run.candidate_hash,
                    "exact_base_revision": run.base_revision,
                    "exact_branch_name": run.branch_name,
                    "exact_head_revision": attestation.head_revision,
                    "default_branch_head_must_equal_base_revision": True,
                    "no_force_push": True,
                    "no_branch_rewrite": True,
                    "merge_exact_review_branch_only": True,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": f"merge:{run.id}",
            "X-HELIS-Candidate-SHA256": run.candidate_hash,
            "X-HELIS-Base-Revision": run.base_revision,
            "X-HELIS-Head-Revision": attestation.head_revision,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return SelfImprovementMergeAck.model_validate_json(response.read().decode("utf-8"))
