from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import Opportunity, ValidationResult
from helis.gtm_domain import Lead, OutreachDraft
from helis.gtm_experiment_domain import GTMExperiment, GTMExperimentArm
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
customers, results, testimonials, guarantees, scarcity or fake discounts. No manipulative subject lines.
The message should make it easy to decline. Do not include tracking or hidden content.
Every lead-specific factual claim must be supported by evidence_ids from that lead.
If a lead has an assigned GTM experiment arm, use exactly those proposed offer terms. An arm is a
prospective test offer, not evidence of an existing price, customer or result. If it contains a price,
state that price clearly and do not add different pricing or invented conditions.
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
        *,
        experiment: GTMExperiment | None = None,
        offer_arms: dict[UUID, GTMExperimentArm] | None = None,
    ) -> list[OutreachDraft]:
        if not leads:
            return []
        assignments = offer_arms or {}
        if assignments and experiment is None:
            raise ValueError("experiment arm assignments require an experiment")
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "validation_results": [
                        item.model_dump(mode="json") for item in validation_results
                    ],
                    "leads": [item.model_dump(mode="json") for item in leads],
                    "gtm_experiment": (
                        experiment.model_dump(mode="json") if experiment is not None else None
                    ),
                    "lead_arm_assignments": {
                        str(lead_id): arm.model_dump(mode="json")
                        for lead_id, arm in assignments.items()
                    },
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
            arm = assignments.get(lead.id)
            drafts.append(
                OutreachDraft(
                    lead_id=lead.id,
                    opportunity_id=opportunity.id,
                    channel=lead.channel,
                    subject=payload.subject,
                    body=payload.body,
                    evidence_ids=payload.evidence_ids,
                    experiment_id=(experiment.id if arm is not None and experiment else None),
                    experiment_arm_key=arm.key if arm is not None else None,
                )
            )
        return drafts
