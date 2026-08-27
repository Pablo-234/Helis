from __future__ import annotations

import json

from pydantic import BaseModel

from helis.budget import CycleBudget
from helis.domain import Experiment, Opportunity, ValidationResult
from helis.model_provider import ModelProvider


class FollowUpEnvelope(BaseModel):
    experiment: Experiment | None = None
    reason: str = ""


SYSTEM_PROMPT = """You are HELIS Follow-up Experiment Designer.
The venture is still in validation because existing results did not justify advance, pivot, or kill.
Design at most ONE next experiment that reduces the most important remaining uncertainty.
Do not repeat an existing experiment in different words. Prefer the cheapest reversible test that
adds genuinely new evidence. Do not invent results.
If no useful new experiment exists, return {"experiment":null,"reason":"..."}.
Otherwise return {"experiment":{...},"reason":"..."} using the HELIS Experiment schema.
Allowed experiment_type values: desk_research, interview, smoke_test, pricing, concierge,
prototype, sales, other.
"""


class FollowUpDesigner:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def design(
        self,
        opportunity: Opportunity,
        existing: list[Experiment],
        results: list[ValidationResult],
    ) -> Experiment | None:
        self.budget.ensure_call_available()
        response = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "existing_experiments": [item.model_dump(mode="json") for item in existing],
                    "validation_results": [item.model_dump(mode="json") for item in results],
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(response)
        envelope = FollowUpEnvelope.model_validate_json(response.content)
        experiment = envelope.experiment
        if experiment is None or experiment.opportunity_id != opportunity.id:
            return None

        signatures = {
            (item.title.strip().lower(), item.hypothesis.strip().lower()) for item in existing
        }
        signature = (experiment.title.strip().lower(), experiment.hypothesis.strip().lower())
        if signature in signatures:
            return None
        return experiment
