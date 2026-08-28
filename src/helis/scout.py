from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import (
    BusinessModelHypothesis,
    Evidence,
    EvidenceKind,
    Observation,
    Opportunity,
)
from helis.model_provider import ModelProvider
from helis.money_model import expand_problem_opportunity


class Candidate(BaseModel):
    title: str
    problem: str
    customer: str
    proposed_value: str
    supporting_observation_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    money_models: list[BusinessModelHypothesis] = Field(default_factory=list, max_length=5)


class CandidateEnvelope(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list, max_length=5)


SYSTEM_PROMPT = """You are the HELIS Opportunity + Monetization Scout.
Find economically testable customer problems in the supplied observations, then propose structurally
different ways to make money from solving each problem.

Evidence rules:
- Do not invent evidence. Every factual support claim must trace to a supplied observation ID.
- Treat prices, margins, time-to-revenue and operating effort as HYPOTHESES, never observed facts.
- If evidence is too weak to support a real problem/customer pair, return no candidate for it.

Business-model rules:
- For each candidate, propose 2-5 meaningfully different money_models when plausible.
- Do NOT assume the solution must be software or even an AI bot.
- Consider managed services, agent-delivered services, automations, data products, marketplaces,
  software, media, physical/operational services and hybrids when they make economic sense.
- Vary the economic mechanism, not merely wording. Prefer different revenue_model + delivery_model
  combinations instead of cosmetic variants.
- Optimize for fast falsification, capital efficiency, strong margins and low recurring owner effort,
  but do not fabricate traction or certainty.
- automation_roles describe capabilities that could later be automated; they are NOT a fixed bot design.
- human_roles must honestly include work that probably still needs a person.
- Use one plausible ISO-4217 currency consistently across money models for the same candidate.

Return JSON only in this shape:
{
  "candidates": [
    {
      "title": "problem-level opportunity name",
      "problem": "specific painful problem",
      "customer": "who experiences the problem",
      "proposed_value": "problem-level outcome, not a specific implementation",
      "supporting_observation_ids": ["UUID"],
      "tags": ["..."],
      "money_models": [
        {
          "name": "specific monetized venture concept",
          "payer": "who pays",
          "offer": "what is sold",
          "value_proposition": "why the payer would buy",
          "revenue_model": "subscription|retainer|fixed_fee|usage|transaction_fee|success_fee|lead_fee|licensing|marketplace_fee|advertising|other",
          "delivery_model": "ai_agent_service|managed_service|software|automation|data_product|marketplace|content_media|physical_ops|hybrid|other",
          "pricing": {"currency":"USD","low_cents":0,"high_cents":0,"unit":"per ..."},
          "acquisition_wedge": "cheapest credible path to first buyers",
          "fulfillment": "how value is actually delivered",
          "automation_roles": ["capability that could be automated"],
          "human_roles": ["work that still requires a person"],
          "time_to_first_revenue_days": 1,
          "gross_margin_pct": 0,
          "owner_minutes_per_week_at_scale": 0,
          "test_cost_cents": 0,
          "primary_risks": ["..." ]
        }
      ]
    }
  ]
}
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
            problem = Opportunity(
                title=candidate.title,
                problem=candidate.problem,
                customer=candidate.customer,
                proposed_value=candidate.proposed_value,
                evidence=evidence,
                tags=candidate.tags,
            )
            if candidate.money_models:
                opportunities.extend(
                    expand_problem_opportunity(problem, candidate.money_models, limit=3)
                )
            else:
                # Backward-compatible fallback for old providers/fixtures. New prompts should
                # normally produce explicit money models before an Opportunity is persisted.
                opportunities.append(problem)
        return opportunities
