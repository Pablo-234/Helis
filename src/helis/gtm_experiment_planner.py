from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import Opportunity, ValidationResult
from helis.gtm_domain import LeadResponse
from helis.gtm_experiment_domain import GTMExperiment, GTMExperimentArm, GTMExperimentKind
from helis.model_provider import ModelProvider


class GTMExperimentArmPayload(BaseModel):
    key: str
    label: str = Field(min_length=2, max_length=100)
    offer_summary: str = Field(min_length=10, max_length=800)
    price_cents: int | None = Field(default=None, ge=100, le=10_000_000)
    currency: str = Field(default="PLN", min_length=3, max_length=3)


class GTMExperimentPlanPayload(BaseModel):
    kind: GTMExperimentKind
    hypothesis: str = Field(min_length=10, max_length=1200)
    arms: list[GTMExperimentArmPayload] = Field(min_length=2, max_length=2)


SYSTEM_PROMPT = """You are HELIS bounded GTM Experiment Planner.
Create exactly ONE small A/B experiment for a venture that already received at least one real GTM response.
The goal is to learn whether a different offer framing/package or explicit price improves market outcomes.
Return exactly two arms named `control` and `variant`. Change ONE primary commercial dimension only.
Use only supplied venture/validation/response evidence. Proposed experiment terms are allowed, but do not
invent existing customers, traction, guarantees, scarcity, discounts that do not exist, or recipient facts.
If there is not enough credible evidence to choose explicit prices, use kind=`offer` and leave price_cents null.
If kind=`pricing`, both arms MUST have explicit prices in the same ISO-4217 currency and the larger price may
be at most 4x the smaller price. Keep terms commercially plausible and easy to explain in one short message.
Return JSON only with: kind, hypothesis, arms[{key,label,offer_summary,price_cents,currency}].
"""


class GTMExperimentPlanner:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def plan(
        self,
        opportunity: Opportunity,
        validation_results: list[ValidationResult],
        responses: list[LeadResponse],
    ) -> GTMExperiment:
        if not responses:
            raise ValueError("GTM experiment planning requires at least one real response")
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "validation_results": [
                        item.model_dump(mode="json") for item in validation_results
                    ],
                    "gtm_responses": [item.model_dump(mode="json") for item in responses[-10:]],
                    "constraints": {
                        "arms": ["control", "variant"],
                        "one_primary_dimension": True,
                        "pricing_max_ratio": 4,
                        "contact_volume_unchanged": True,
                    },
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = GTMExperimentPlanPayload.model_validate_json(result.content)
        arms = [
            GTMExperimentArm(
                key=item.key,
                label=item.label,
                offer_summary=item.offer_summary,
                price_cents=item.price_cents,
                currency=item.currency.upper(),
            )
            for item in payload.arms
        ]
        return GTMExperiment(
            opportunity_id=opportunity.id,
            kind=payload.kind,
            hypothesis=payload.hypothesis,
            arms=arms,
        )
