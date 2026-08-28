from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.commerce_domain import CommerceBuildContext
from helis.domain import (
    BuildBundle,
    BuildReview,
    BuildReviewVerdict,
    BuildRun,
    BuildSpec,
    Opportunity,
)
from helis.model_provider import ModelProvider


class BuildReviewPayload(BaseModel):
    verdict: BuildReviewVerdict
    score: float = Field(ge=0, le=10)
    blocking_issues: list[str] = Field(default_factory=list, max_length=12)
    warnings: list[str] = Field(default_factory=list, max_length=12)
    summary: str = Field(min_length=3, max_length=2000)


SYSTEM_PROMPT = """You are HELIS Adversarial Build Reviewer.
Review one generated MVP as if you wanted to prevent a bad venture artifact from reaching preview.
Look for: mismatch with the validated problem, unsupported/fabricated claims, deceptive copy,
privacy/security hazards, missing acceptance criteria, unusable instructions and needless scope.
If approved_commerce is supplied, verify that the artifact presents the exact approved visible price
and links only to the exact approved checkout URL. Any different price, currency, billing promise,
checkout destination, payment script/widget/iframe or alternate external HTTP(S) destination is a
blocking issue. A normal anchor to the exact approved checkout URL is allowed.
For python_service_v1, deterministic static checks and isolated unittest execution have already run.
Still inspect whether handle(request)->dict actually implements useful bounded venture logic, whether
success/failure tests meaningfully exercise the stated contract, whether edge cases are handled
without misleading behavior, and whether the README accurately describes limitations. Do not treat
passing tests as proof that the business value is correct, secure for production, or ready to deploy.
Do not suggest adding network, credentials, dependencies, deployment, persistence or broader runtime
authority merely to improve the score. Do not reward visual polish over correctness.
Do not modify files and do not invent evidence.
Return JSON only: verdict (pass|fail), score 0-10, blocking_issues, warnings, summary.
A blocking issue must produce verdict=fail.
"""


class AdversarialBuildReviewer:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def review(
        self,
        opportunity: Opportunity,
        spec: BuildSpec,
        run: BuildRun,
        bundle: BuildBundle,
        commerce: CommerceBuildContext | None = None,
    ) -> BuildReview:
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "build_spec": spec.model_dump(mode="json"),
                    "approved_commerce": (
                        commerce.model_dump(mode="json") if commerce is not None else None
                    ),
                    "files": [item.model_dump(mode="json") for item in bundle.files],
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = BuildReviewPayload.model_validate_json(result.content)
        verdict = payload.verdict
        if payload.blocking_issues or payload.score < 7:
            verdict = BuildReviewVerdict.FAIL
        return BuildReview(
            run_id=run.id,
            verdict=verdict,
            score=payload.score,
            blocking_issues=payload.blocking_issues,
            warnings=payload.warnings,
            summary=payload.summary,
        )
