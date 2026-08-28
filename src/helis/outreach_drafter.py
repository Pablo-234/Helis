from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import Opportunity, ValidationResult
from helis.gtm_domain import Lead, OutreachDraft
from helis.model_provider import ModelProvider


class DraftPayload(BaseModel):
    lead_id: UUID
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=20, max_length=4000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=12)


class DraftEnvelope(BaseModel):
    drafts: list[DraftPayload] = Field(default_factory=list, max_length=5)


SYSTEM_PROMPT = """You are HELIS B2B Outreach Writer.
Create short, respectful first-contact drafts for the supplied organizations.
Use only facts present in each lead's evidence plus the validated venture evidence.
Do not pretend to know the recipient personally. Do not fabricate personalization, urgency,
customers, results, testimonials or guarantees. No manipulative subject lines.
The message should make it easy to decline. Do not include tracking or hidden content.
Every lead-specific factual claim must be supported by evidence_ids from that lead.
Return JSON only: {"drafts":[{"lead_id":"...","subject":"...","body":"...","evidence_ids":[]}]}
"""


class OutreachDrafter:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def draft(
        self,
        opportunity: Opportunity,
        validation_results: list[ValidationResult],
        leads: list[Lead],
    ) -> list[OutreachDraft]:
        if not leads:
            return []
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "validation_results": [item.model_dump(mode="json") for item in validation_results],
                    "leads": [item.model_dump(mode="json") for item in leads],
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        envelope = DraftEnvelope.model_validate_json(result.content)
        by_id = {lead.id: lead for lead in leads}
        drafts: list[OutreachDraft] = []
        for payload in envelope.drafts:
            lead = by_id.get(payload.lead_id)
            if lead is None:
                continue
            known_evidence = {item.id for item in lead.evidence}
            if not set(payload.evidence_ids) <= known_evidence:
                continue
            drafts.append(
                OutreachDraft(
                    lead_id=lead.id,
                    opportunity_id=opportunity.id,
                    channel=lead.channel,
                    subject=payload.subject,
                    body=payload.body,
                    evidence_ids=payload.evidence_ids,
                )
            )
        return drafts
