from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import Experiment, Opportunity, SkepticReport
from helis.model_provider import ModelProvider


class ExperimentEnvelope(BaseModel):
    experiments: list[Experiment] = Field(default_factory=list, max_length=5)


SYSTEM_PROMPT = """You are HELIS Experiment Designer.
Design the cheapest experiments that can falsify the riskiest assumptions of a venture.
Do not optimize for impressive demos. Optimize for information gained per unit of money and effort.
Prefer reversible tests. Do not claim an experiment is free if it needs paid traffic, a paid service,
or meaningful external spending.
Do not invent experiment results.
Return at most 3 experiments as JSON: {"experiments":[...]}.
Every experiment must include: opportunity_id, title, experiment_type, hypothesis,
success_metric, success_threshold, targeted_assumptions (zero-based indexes into the supplied
skeptic assumptions), expected_information_gain 0-10, effort_score 0-10, max_cost_cents,
max_duration_hours, requires_external_contact, requires_publication.
Allowed experiment_type values: desk_research, interview, smoke_test, pricing, concierge,
prototype, sales, other.
"""


class ExperimentDesigner:
    def __init__(self, provider: ModelProvider, budget: CycleBudget | None = None) -> None:
        self.provider = provider
        self.budget = budget or CycleBudget()

    def design(
        self,
        opportunity: Opportunity,
        skeptic_report: SkepticReport,
    ) -> list[Experiment]:
        self.budget.ensure_call_available()
        payload = {
            "opportunity": opportunity.model_dump(mode="json"),
            "skeptic_report": skeptic_report.model_dump(mode="json"),
        }
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user="VALIDATION_INPUT:\n" + json.dumps(payload, ensure_ascii=False),
        )
        self.budget.record(result)
        envelope = ExperimentEnvelope.model_validate_json(result.content)

        valid: list[Experiment] = []
        assumption_count = len(skeptic_report.assumptions)
        for experiment in envelope.experiments:
            if experiment.opportunity_id != opportunity.id:
                continue
            if any(index < 0 or index >= assumption_count for index in experiment.targeted_assumptions):
                continue
            valid.append(experiment)
        return valid
