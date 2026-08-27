from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import Opportunity, ScoreDimensions
from helis.model_provider import ModelProvider


class AnalystAssessment(BaseModel):
    dimensions: ScoreDimensions
    rationale: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are the HELIS Venture Analyst.
Score one opportunity using ONLY the evidence attached to it.
Each dimension is 0-10. execution_risk is inverted semantically: 10 means very risky.
Unknown facts are not permission to guess: lower evidence_strength and list the uncertainty.
Be skeptical about willingness_to_pay, market_access and competition_gap unless evidence supports them.
The arithmetic and final recommendation are computed outside you.
Return JSON only:
{"dimensions":{"pain":0,"frequency":0,"willingness_to_pay":0,"market_access":0,"automation_fit":0,"speed_to_test":0,"competition_gap":0,"evidence_strength":0,"capital_efficiency":0,"execution_risk":0},"rationale":[],"uncertainties":[]}.
"""


class OpportunityAnalyst:
    def __init__(self, provider: ModelProvider, budget: CycleBudget | None = None) -> None:
        self.provider = provider
        self.budget = budget or CycleBudget()

    def assess(self, opportunity: Opportunity) -> AnalystAssessment:
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user="OPPORTUNITY:\n" + json.dumps(opportunity.model_dump(mode="json"), ensure_ascii=False),
        )
        self.budget.record(result)
        return AnalystAssessment.model_validate_json(result.content)
