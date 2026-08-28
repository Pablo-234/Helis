from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.build_templates import get_template, template_catalog
from helis.domain import BuildSpec, BuildTemplate, Opportunity, ValidationResult
from helis.model_provider import ModelProvider


class BuildPlanPayload(BaseModel):
    template: BuildTemplate
    name: str = Field(min_length=3, max_length=120)
    goal: str = Field(min_length=10, max_length=1200)
    acceptance_criteria: list[str] = Field(min_length=2, max_length=8)


SYSTEM_PROMPT = """You are HELIS MVP Planner.
Turn one VALIDATED venture into the smallest useful MVP build brief.
Choose exactly one template from the supplied catalog. Do not request custom infrastructure,
credentials, payments, deployment, external scripts, tracking pixels or new dependencies.
The purpose of this build is to make the validated value proposition testable, not to imitate a
finished company. Never invent traction, testimonials, customers, certifications or measured
results that are not present in the supplied evidence.
Return JSON only with: template, name, goal, acceptance_criteria.
"""


class BuilderPlanner:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def plan(
        self,
        opportunity: Opportunity,
        validation_results: list[ValidationResult],
    ) -> BuildSpec:
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "validation_results": [
                        item.model_dump(mode="json") for item in validation_results
                    ],
                    "template_catalog": template_catalog(),
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = BuildPlanPayload.model_validate_json(result.content)
        definition = get_template(payload.template)
        return BuildSpec(
            opportunity_id=opportunity.id,
            template=payload.template,
            name=payload.name,
            goal=payload.goal,
            acceptance_criteria=payload.acceptance_criteria,
            max_files=definition.max_files,
            max_total_bytes=definition.max_total_bytes,
        )
