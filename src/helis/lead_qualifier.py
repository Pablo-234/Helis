from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import Opportunity
from helis.gtm_domain import Lead
from helis.model_provider import ModelProvider


class LeadAssessment(BaseModel):
    lead_id: UUID
    fit_score: float = Field(ge=0, le=10)
    rationale: list[str] = Field(default_factory=list, max_length=6)
    supporting_evidence_ids: list[UUID] = Field(default_factory=list, max_length=12)


class LeadAssessmentEnvelope(BaseModel):
    assessments: list[LeadAssessment] = Field(default_factory=list, max_length=25)


SYSTEM_PROMPT = """You are HELIS B2B Lead Qualifier.
Score candidate organizations only from the supplied venture and candidate evidence.
Do not infer private facts. Do not invent company size, budget, tools, pain, contact details or intent.
A fit score above zero must cite evidence IDs supplied for that lead. Return JSON only with
assessments: lead_id, fit_score 0-10, rationale, supporting_evidence_ids.
"""


class LeadQualifier:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def qualify(self, opportunity: Opportunity, leads: list[Lead]) -> list[LeadAssessment]:
        if not leads:
            return []
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "leads": [item.model_dump(mode="json") for item in leads],
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        envelope = LeadAssessmentEnvelope.model_validate_json(result.content)
        by_id = {lead.id: lead for lead in leads}
        valid: list[LeadAssessment] = []
        for assessment in envelope.assessments:
            lead = by_id.get(assessment.lead_id)
            if lead is None:
                continue
            known_evidence = {item.id for item in lead.evidence}
            if not set(assessment.supporting_evidence_ids) <= known_evidence:
                continue
            if assessment.fit_score > 0 and not assessment.supporting_evidence_ids:
                continue
            valid.append(assessment)
        return valid
