from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import ClassVar, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from helis.self_improvement_domain import (
    EvaluationSnapshot,
    SelfImprovementCandidate,
    SelfImprovementProposal,
)


class SelfImprovementGatewayConfigurationError(ValueError):
    pass


class EvaluationGatewayResponse(BaseModel):
    candidate_hash: str = Field(min_length=64, max_length=64)
    metric_name: str = Field(min_length=2, max_length=120)
    baseline_file_hashes: dict[str, str] = Field(default_factory=dict)
    baseline: EvaluationSnapshot
    candidate: EvaluationSnapshot
    regressions: list[str] = Field(default_factory=list, max_length=30)


class SelfImprovementEvaluationGateway(Protocol):
    name: str
    safe_destination: str

    def evaluate(
        self,
        proposal: SelfImprovementProposal,
        candidate: SelfImprovementCandidate,
    ) -> EvaluationGatewayResponse: ...


def _validate_url(url: str, *, allow_insecure_local: bool = False) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise SelfImprovementGatewayConfigurationError("self-eval gateway URL must include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SelfImprovementGatewayConfigurationError(
            "self-eval gateway URL may not contain credentials, query parameters or fragments"
        )
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and allow_insecure_local
        and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise SelfImprovementGatewayConfigurationError(
        "self-eval gateway must use HTTPS; local HTTP requires explicit development opt-in"
    )


@dataclass(slots=True)
class ApprovedSelfImprovementEvaluationGateway:
    """Operator-routed isolated evaluator. It cannot merge or mutate the live HELIS checkout."""

    name: ClassVar[str] = "approved_self_improvement_eval_gateway_v1"
    url: str
    token: str = ""
    timeout_seconds: int = 120
    allow_insecure_local: bool = False

    def __post_init__(self) -> None:
        _validate_url(self.url, allow_insecure_local=self.allow_insecure_local)

    @classmethod
    def from_env(cls) -> ApprovedSelfImprovementEvaluationGateway | None:
        url = os.getenv("HELIS_SELF_EVAL_GATEWAY_URL", "").strip()
        if not url:
            return None
        return cls(
            url=url,
            token=os.getenv("HELIS_SELF_EVAL_GATEWAY_TOKEN", ""),
            timeout_seconds=int(os.getenv("HELIS_SELF_EVAL_GATEWAY_TIMEOUT", "120")),
            allow_insecure_local=os.getenv("HELIS_ALLOW_INSECURE_LOCAL_SELF_EVAL_GATEWAY", "0")
            == "1",
        )

    @property
    def safe_destination(self) -> str:
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"

    def evaluate(
        self,
        proposal: SelfImprovementProposal,
        candidate: SelfImprovementCandidate,
    ) -> EvaluationGatewayResponse:
        payload = json.dumps(
            {
                "contract_version": 1,
                "proposal": proposal.model_dump(mode="json"),
                "candidate": {
                    "id": str(candidate.id),
                    "candidate_hash": candidate.candidate_hash,
                    "files": [item.model_dump(mode="json") for item in candidate.files],
                },
                "constraints": {
                    "attest_exact_baseline_file_hashes": True,
                    "immutable_baseline_tests": True,
                    "same_test_suite_for_baseline_and_candidate": True,
                    "candidate_network_disabled": True,
                    "candidate_cannot_modify_tests": True,
                    "no_merge": True,
                    "no_live_checkout_write": True,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": str(candidate.id),
            "X-HELIS-Candidate-SHA256": candidate.candidate_hash,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return EvaluationGatewayResponse.model_validate_json(response.read().decode("utf-8"))
