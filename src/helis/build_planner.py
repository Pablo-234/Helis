from __future__ import annotations

import json

from helis.budget import CycleBudget
from helis.build_domain import BuildSpec
from helis.domain import Opportunity, ValidationResult
from helis.model_provider import ModelProvider


SYSTEM_PROMPT = """You are HELIS MVP Build Planner.
Convert a VALIDATED venture into the smallest useful build specification.
Do not add unvalidated features. Do not request payments, credentials, customer outreach,
production deployment, external APIs, package dependencies, shell commands, or infrastructure.
Choose runtime only from: static_web, python_stdlib.
Prefer static_web when a clickable/local interface is enough; use python_stdlib only when real
business logic is essential. Keep core_flows <= 6 and acceptance_criteria <= 10.
Return JSON matching BuildSpec exactly.
"""


class BuildPlanner:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def plan(self, opportunity: Opportunity, results: list[ValidationResult]) -> BuildSpec:
        self.budget.ensure_call_available()
        response = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "validation_results": [item.model_dump(mode="json") for item in results],
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(response)
        spec = BuildSpec.model_validate_json(response.content)
        if spec.opportunity_id != opportunity.id:
            raise ValueError("build spec opportunity_id does not match the validated venture")
        return spec
