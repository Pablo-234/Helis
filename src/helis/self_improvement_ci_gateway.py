from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from helis.self_improvement_branch_domain import BranchMaterializationRun
from helis.self_improvement_domain import SelfImprovementCandidate
from helis.self_improvement_merge_domain import (
    SelfImprovementCIAttestation,
    SelfImprovementMergeRun,
)


class SelfImprovementCIGatewayConfigurationError(ValueError):
    pass


class SelfImprovementCIGateway(Protocol):
    name: str
    safe_destination: str

    def attest(
        self,
        run: SelfImprovementMergeRun,
        branch_run: BranchMaterializationRun,
        candidate: SelfImprovementCandidate,
    ) -> SelfImprovementCIAttestation: ...


def _validate_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise SelfImprovementCIGatewayConfigurationError("self-CI gateway URL must include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SelfImprovementCIGatewayConfigurationError(
            "self-CI gateway URL may not contain credentials, query parameters or fragments"
        )
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and allow_insecure_local
        and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise SelfImprovementCIGatewayConfigurationError(
        "self-CI gateway must use HTTPS; local HTTP requires explicit development opt-in"
    )


@dataclass(slots=True)
class ApprovedSelfImprovementCIGateway:
    """Read-only attestation boundary for an exact review branch and candidate."""

    name: ClassVar[str] = "approved_self_improvement_ci_gateway_v1"
    url: str
    token: str = ""
    timeout_seconds: int = 120
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedSelfImprovementCIGateway | None:
        url = os.getenv("HELIS_SELF_CI_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_SELF_CI_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_SELF_CI_GATEWAY_TIMEOUT", "120")),
            allow_insecure_local=os.getenv("HELIS_ALLOW_INSECURE_LOCAL_SELF_CI_GATEWAY", "0")
            == "1",
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def attest(
        self,
        run: SelfImprovementMergeRun,
        branch_run: BranchMaterializationRun,
        candidate: SelfImprovementCandidate,
    ) -> SelfImprovementCIAttestation:
        payload = json.dumps(
            {
                "contract_version": 1,
                "run": {
                    "id": str(run.id),
                    "candidate_hash": run.candidate_hash,
                    "base_revision": run.base_revision,
                    "branch_name": run.branch_name,
                },
                "branch_run": branch_run.model_dump(mode="json"),
                "candidate": {
                    "id": str(candidate.id),
                    "candidate_hash": candidate.candidate_hash,
                    "files": [item.model_dump(mode="json") for item in candidate.files],
                },
                "constraints": {
                    "read_only": True,
                    "same_branch": run.branch_name,
                    "exact_candidate_hash": run.candidate_hash,
                    "required_checks": ["ruff", "pytest"],
                    "attest_candidate_file_hashes": True,
                    "no_merge": True,
                    "no_branch_mutation": True,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": f"ci:{run.id}",
            "X-HELIS-Candidate-SHA256": run.candidate_hash,
            "X-HELIS-Branch": run.branch_name,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return SelfImprovementCIAttestation.model_validate_json(response.read().decode("utf-8"))
