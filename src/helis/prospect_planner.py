from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import Opportunity, ValidationResult
from helis.gtm_domain import ProspectQuery
from helis.model_provider import ModelProvider


class ProspectPlanEnvelope(BaseModel):
    queries: list[ProspectQuery] = Field(min_length=1, max_length=3)


SYSTEM_PROMPT = """You are HELIS Prospect Planner.
Turn one validated venture into up to 3 narrow B2B prospect searches.
Search for organizations that have observable reasons to experience the validated problem.
Do not search for private individuals. Prefer public business contact surfaces.
Do not invent prospects or contact details; you only create search queries.
Keep max_results small. Return JSON only: {"queries":[...]}.
Each query must contain opportunity_id, query, target_customer, must_have_signals,
disqualifiers and max_results.
"""


class ProspectPlanner:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def plan(
        self,
        opportunity: Opportunity,
        validation_results: list[ValidationResult],
    ) -> list[ProspectQuery]:
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "validation_results": [item.model_dump(mode="json") for item in validation_results],
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        envelope = ProspectPlanEnvelope.model_validate_json(result.content)
        return [item for item in envelope.queries if item.opportunity_id == opportunity.id]
