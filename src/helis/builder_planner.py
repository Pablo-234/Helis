from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.build_templates import get_template, template_catalog
from helis.commerce_domain import CommerceBuildContext
from helis.domain import BuildSpec, BuildTemplate, Opportunity, ValidationResult
from helis.model_provider import ModelProvider


class BuildPlanningError(RuntimeError):
    pass


class BuildPlanPayload(BaseModel):
    template: BuildTemplate
    name: str = Field(min_length=3, max_length=120)
    goal: str = Field(min_length=10, max_length=1200)
    acceptance_criteria: list[str] = Field(min_length=2, max_length=8)


SYSTEM_PROMPT = """You are HELIS MVP Planner.
Turn one VALIDATED venture into the smallest useful MVP build brief.
Choose exactly one template from the supplied catalog. Do not request custom infrastructure,
credentials, deployment, tracking pixels or new dependencies.
The purpose of this build is to make the validated value proposition testable, not to imitate a
finished company. Never invent traction, testimonials, customers, certifications or measured
results that are not present in the supplied evidence.
If approved_commerce is null, do not request payments or checkout integration.
If approved_commerce is supplied, HELIS will deterministically force static_web_v1. Treat the exact
checkout URL, exact visible price and billing mode as immutable operator-approved inputs. The page
may link to that exact HTTPS checkout using a normal anchor only; do not request scripts, forms,
embedded payment widgets, alternate payment URLs or a different price.
If python_service_v1 is present in the catalog, use it only when executable workflow logic is
material to testing the validated value; otherwise prefer the simpler static/manual template.
Return JSON only with: template, name, goal, acceptance_criteria.
"""


_DEFAULT_TEMPLATES = {BuildTemplate.STATIC_WEB, BuildTemplate.CONCIERGE_OPS}


class BuilderPlanner:
    def __init__(
        self,
        provider: ModelProvider,
        budget: CycleBudget,
        *,
        enabled_templates: set[BuildTemplate] | None = None,
    ) -> None:
        self.provider = provider
        self.budget = budget
        self.enabled_templates = set(enabled_templates or _DEFAULT_TEMPLATES)

    def plan(
        self,
        opportunity: Opportunity,
        validation_results: list[ValidationResult],
        commerce: CommerceBuildContext | None = None,
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
                    "template_catalog": template_catalog(self.enabled_templates),
                    "approved_commerce": (
                        commerce.model_dump(mode="json") if commerce is not None else None
                    ),
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = BuildPlanPayload.model_validate_json(result.content)
        selected_template = (
            BuildTemplate.STATIC_WEB if commerce is not None else payload.template
        )
        if selected_template not in self.enabled_templates:
            raise BuildPlanningError(
                f"planner selected unavailable build template: {selected_template.value}"
            )
        definition = get_template(selected_template)
        acceptance = list(payload.acceptance_criteria)
        if commerce is not None:
            required = (
                f"Show exact approved price {commerce.display_price} and link only to the approved "
                "checkout URL"
            )
            if required not in acceptance:
                acceptance = [*acceptance[:7], required]
        return BuildSpec(
            opportunity_id=opportunity.id,
            template=selected_template,
            name=payload.name,
            goal=payload.goal,
            acceptance_criteria=acceptance,
            max_files=definition.max_files,
            max_total_bytes=definition.max_total_bytes,
        )
