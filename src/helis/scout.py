from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import Evidence, EvidenceKind, Observation, Opportunity
from helis.model_provider import ModelProvider


class Candidate(BaseModel):
    title: str
    problem: str
    customer: str
    proposed_value: str
    supporting_observation_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CandidateEnvelope(BaseModel):
    candidates: list[Candidate]


SYSTEM_PROMPT = """You are the HELIS Opportunity Scout.
Find economically testable business opportunities in the supplied observations.
Do not invent evidence. Every factual support claim must trace to one of the supplied observation IDs.
Prefer painful, frequent, expensive or slow workflows and clear market inefficiencies.
Do not assume the solution must be software.
Return JSON only: {\"candidates\":[{\"title\":...,\"problem\":...,\"customer\":...,\"proposed_value\":...,\"supporting_observation_ids\":[...],\"tags\":[...]}]}.
If evidence is too weak, return an empty candidates array.
"""


class OpportunityScout:
    def __init__(self, provider: ModelProvider, budget: CycleBudget | None = None) -> None:
        self.provider = provider
        self.budget = budget or CycleBudget()

    def discover(self, observations: list[Observation]) -> list[Opportunity]:
        if not observations:
            return []
        self.budget.ensure_call_available()

        observation_map = {item.id: item for item in observations}
        payload = [
            {
                "id": str(item.id),
                "source": item.source,
                "text": item.text,
                "metadata": item.metadata,
            }
            for item in observations
        ]
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user="OBSERVATIONS:\n" + json.dumps(payload, ensure_ascii=False),
        )
        self.budget.record(result)
        envelope = CandidateEnvelope.model_validate_json(result.content)

        opportunities: list[Opportunity] = []
        for candidate in envelope.candidates:
            valid_observations = [
                observation_map[item_id]
                for item_id in candidate.supporting_observation_ids
                if item_id in observation_map
            ]
            if not valid_observations:
                continue

            evidence = [
                Evidence(
                    kind=EvidenceKind.OTHER,
                    claim=item.text,
                    source=item.source,
                    observation_id=item.id,
                    confidence=0.5,
                    observed_at=item.captured_at,
                )
                for item in valid_observations
            ]
            opportunities.append(
                Opportunity(
                    title=candidate.title,
                    problem=candidate.problem,
                    customer=candidate.customer,
                    proposed_value=candidate.proposed_value,
                    evidence=evidence,
                    tags=candidate.tags,
                )
            )
        return opportunities
