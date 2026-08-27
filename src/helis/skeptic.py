from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import Assumption, Opportunity, SkepticReport
from helis.model_provider import ModelProvider


class SkepticResponse(BaseModel):
    assumptions: list[Assumption] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are HELIS Skeptic, an adversarial venture reviewer.
Your job is to try to falsify the opportunity, not to sell it.
Use ONLY the supplied opportunity and attached evidence.
Never turn missing evidence into a factual claim.
Identify assumptions that can make the business fail, especially demand, pain frequency,
willingness to pay, customer access, competition, economics, legal/operational feasibility and
whether the proposed solution is actually better than existing behavior.
For each assumption provide:
- statement: what HELIS is implicitly assuming
- failure_mode: what goes wrong if false
- falsifier: observable evidence that would show it is false
- criticality: 0-10 impact on the venture
- uncertainty: 0-10 how poorly supported it currently is
Return JSON only:
{"assumptions":[],"contradictions":[],"missing_evidence":[]}.
"""


class VentureSkeptic:
    def __init__(self, provider: ModelProvider, budget: CycleBudget | None = None) -> None:
        self.provider = provider
        self.budget = budget or CycleBudget()

    def review(self, opportunity: Opportunity) -> SkepticReport:
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user="OPPORTUNITY:\n" + json.dumps(opportunity.model_dump(mode="json"), ensure_ascii=False),
        )
        self.budget.record(result)
        response = SkepticResponse.model_validate_json(result.content)
        return SkepticReport(
            opportunity_id=opportunity.id,
            assumptions=response.assumptions,
            contradictions=response.contradictions,
            missing_evidence=response.missing_evidence,
        )
