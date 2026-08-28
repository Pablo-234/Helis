from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from helis.self_improvement_branch_domain import BranchMaterializationRun
from helis.self_improvement_domain import (
    SelfImprovementCandidate,
    SelfImprovementEvaluation,
    SelfImprovementProposal,
)


class SelfImprovementBranchGatewayConfigurationError(ValueError):
    pass


class BranchMaterializationAck(BaseModel):
    candidate_hash: str = Field(min_length=64, max_length=64)
    base_revision: str = Field(min_length=40, max_length=40)
    branch_name: str = Field(min_length=8, max_length=160)
    external_ref: str = Field(min_length=1, max_length=1000)


class SelfImprovementBranchGateway(Protocol):
    name: str
    safe_destination: str

    def materialize(
        self,
        run: BranchMaterializationRun,
        proposal: SelfImprovementProposal,
        candidate: SelfImprovementCandidate,
        evaluation: SelfImprovementEvaluation,
    ) -> BranchMaterializationAck: ...


def _validate_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise SelfImprovementBranchGatewayConfigurationError("branch gateway URL must include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SelfImprovementBranchGatewayConfigurationError(
            "branch gateway URL may not contain credentials, query parameters or fragments"
        )
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and allow_insecure_local
        and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise SelfImprovementBranchGatewayConfigurationError(
        "branch gateway must use HTTPS; local HTTP requires explicit development opt-in"
    )


@dataclass(slots=True)
class ApprovedSelfImprovementBranchGateway:
    """Creates only a review branch from an approved hash-locked candidate; never merges it."""

    name: ClassVar[str] = "approved_self_improvement_branch_gateway_v1"
    url: str
    token: str = ""
    timeout_seconds: int = 120
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedSelfImprovementBranchGateway | None:
        url = os.getenv("HELIS_SELF_BRANCH_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_SELF_BRANCH_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_SELF_BRANCH_GATEWAY_TIMEOUT", "120")),
            allow_insecure_local=os.getenv("HELIS_ALLOW_INSECURE_LOCAL_SELF_BRANCH_GATEWAY", "0")
            == "1",
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def materialize(
        self,
        run: BranchMaterializationRun,
        proposal: SelfImprovementProposal,
        candidate: SelfImprovementCandidate,
        evaluation: SelfImprovementEvaluation,
    ) -> BranchMaterializationAck:
        payload = json.dumps(
            {
                "contract_version": 1,
                "run": run.model_dump(mode="json"),
                "proposal": {
                    "id": str(proposal.id),
                    "objective": proposal.objective,
                    "target_files": proposal.target_files,
                },
                "evaluation": {
                    "id": str(evaluation.id),
                    "accepted": evaluation.accepted,
                    "candidate_hash": evaluation.candidate_hash,
                },
                "candidate": {
                    "id": str(candidate.id),
                    "candidate_hash": candidate.candidate_hash,
                    "files": [item.model_dump(mode="json") for item in candidate.files],
                },
                "constraints": {
                    "branch_only": True,
                    "exact_base_revision": run.base_revision,
                    "exact_branch_name": run.branch_name,
                    "exact_candidate_hash": run.candidate_hash,
                    "no_merge": True,
                    "no_default_branch_write": True,
                    "no_test_mutation": True,
                    "no_additional_files": True,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": str(run.id),
            "X-HELIS-Candidate-SHA256": run.candidate_hash,
            "X-HELIS-Base-Revision": run.base_revision,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return BranchMaterializationAck.model_validate_json(response.read().decode("utf-8"))
