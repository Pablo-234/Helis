from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from helis.budget import CycleBudget
from helis.domain import (
    BusinessModelHypothesis,
    DeliveryModel,
    Evidence,
    EvidenceKind,
    Observation,
    Opportunity,
)
from helis.model_provider import ModelProvider, ModelResponseError
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
    candidates: list[Candidate] = Field(default_factory=list, max_length=2)


class MoneyModelEnvelope(BaseModel):
    money_models: list[BusinessModelHypothesis] = Field(default_factory=list, max_length=3)


SYSTEM_PROMPT = """You are the HELIS Opportunity + Monetization Scout.
Find economically testable customer problems in the supplied observations, then propose structurally
different ways to make money from solving each problem.

Evidence rules:
- Do not invent evidence. Every factual support claim must trace to a supplied observation ID.
- Treat prices, margins, time-to-revenue and operating effort as HYPOTHESES, never observed facts.
- If evidence is too weak to support a real problem/customer pair, return no candidate for it.

Business-model rules:
- Return at most 2 candidates and propose exactly 2 meaningfully different money_models for each.
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

ONLINE_ONLY_PROMPT = """

ONLINE-VENTURE MODE IS ACTIVE.
Every persisted money model must be an online business that can be sold and primarily delivered from
an internet-connected computer. Prefer software, AI/automation services, remote managed services,
data/information products, licensing, marketplaces and digital media.
Do not propose ventures that depend on physical inventory, manufacturing, food, transport, property,
on-site labor, local presence or other location-dependent physical fulfillment.
Use only delivery_model values that clearly fit remote online delivery; do not use physical_ops or
hybrid in this mode.
"""

EMPTY_RESULT_RETRY_PROMPT = """

THE PREVIOUS SCOUT PASS PRODUCED NO USABLE CANDIDATE. This can mean an empty result, invalid
structured output, missing evidence references or a delivery model rejected by online-only policy.
Perform one focused PROBLEM-EXTRACTION pass. Do not design the solution, pricing or business model
yet; a separate monetization step will do that.
A candidate at this stage is a falsifiable BUSINESS HYPOTHESIS, not a validated fact. Weak evidence
is acceptable for discovery when uncertainty is stated honestly; it will be challenged by the
analyst and skeptic later. Do not invent observations, traction, prices or certainty.

Look specifically for an observed workaround, repeated question, manual workflow, unmet request,
cost, delay, coordination burden or group trying to achieve an outcome. Select the single strongest
relative signal. If any supplied observation contains a person or organization trying to achieve
something, return exactly one candidate citing its exact UUID. Use this compact JSON shape:
{"candidates":[{"title":"...","problem":"...","customer":"...","proposed_value":"...",
"supporting_observation_ids":["UUID"],"tags":["hypothesis"],"money_models":[]}]}
Return an empty candidates list only when every supplied observation is empty or entirely unrelated
to a user, organization, workflow, request, project, cost, delay or desired outcome.
"""

MALFORMED_RESULT_REPAIR_PROMPT = """

THE RECOVERY RESPONSE WAS NOT VALID JSON OR DID NOT MATCH THE REQUIRED SCHEMA.
This is the final structured-output repair attempt. Return JSON only, with no Markdown, commentary,
trailing commas or unescaped line breaks. Return exactly one compact problem candidate with one
exact supplied observation UUID and an empty money_models list. Keep every string short. Use the
exact compact shape from the recovery instructions.
"""

MONEY_MODEL_PROMPT = """You are the HELIS Online Monetization Designer.
You receive one evidence-bound problem hypothesis. Propose exactly two structurally different ways
to earn money by solving it entirely online. These are hypotheses for later validation, not facts.
Do not invent traction or evidence. Prefer a fast-to-sell managed/agent service and a more scalable
software, automation, data, marketplace, licensing or media model. Use plausible ISO-4217 currency.

Return JSON only in this shape:
{"money_models":[
  {"name":"...","payer":"...","offer":"...","value_proposition":"...",
   "revenue_model":"subscription|retainer|fixed_fee|usage|transaction_fee|success_fee|lead_fee|licensing|marketplace_fee|advertising|other",
   "delivery_model":"ai_agent_service|managed_service|software|automation|data_product|marketplace|content_media|other",
   "pricing":{"currency":"USD","low_cents":0,"high_cents":0,"unit":"per ..."},
   "acquisition_wedge":"...","fulfillment":"...","automation_roles":["..."],
   "human_roles":["..."],"time_to_first_revenue_days":1,"gross_margin_pct":0,
   "owner_minutes_per_week_at_scale":0,"test_cost_cents":0,"primary_risks":["..."]}
]}
"""

MONEY_MODEL_REPAIR_PROMPT = """
THE PREVIOUS MONETIZATION RESPONSE WAS EMPTY OR MALFORMED. Return exactly two complete online money
models in the requested JSON shape. JSON only; use short strings, no Markdown and no trailing commas.
The numbers are explicitly unvalidated hypotheses, so uncertainty is not a reason to return empty.
"""

ONLINE_DELIVERY_MODELS = frozenset(
    {
        DeliveryModel.AI_AGENT_SERVICE,
        DeliveryModel.MANAGED_SERVICE,
        DeliveryModel.SOFTWARE,
        DeliveryModel.AUTOMATION,
        DeliveryModel.DATA_PRODUCT,
        DeliveryModel.MARKETPLACE,
        DeliveryModel.CONTENT_MEDIA,
    }
)


class OpportunityScout:
    def __init__(
        self,
        provider: ModelProvider,
        budget: CycleBudget | None = None,
        *,
        online_only: bool = False,
    ) -> None:
        self.provider = provider
        self.budget = budget or CycleBudget()
        self.online_only = online_only

    def discover(self, observations: list[Observation]) -> list[Opportunity]:
        if not observations:
            return []

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
        system_prompt = SYSTEM_PROMPT + (ONLINE_ONLY_PROMPT if self.online_only else "")
        user_prompt = "OBSERVATIONS:\n" + json.dumps(payload, ensure_ascii=False)
        try:
            envelope = self._request(system=system_prompt, user=user_prompt)
        except (ModelResponseError, ValidationError):
            envelope = None
        if envelope is not None:
            opportunities = self._opportunities(envelope, observation_map)
            if opportunities:
                return opportunities
            candidates = self._evidence_bound_candidates(envelope, observation_map)
            if candidates:
                return self._monetize(candidates[0], observation_map)

        envelope = self._recover_problem_candidate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        if envelope is None:
            return []
        opportunities = self._opportunities(envelope, observation_map)
        if opportunities:
            return opportunities
        candidates = self._evidence_bound_candidates(envelope, observation_map)
        return self._monetize(candidates[0], observation_map) if candidates else []

    def _recover_problem_candidate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> CandidateEnvelope | None:
        try:
            return self._request(
                system=system_prompt + EMPTY_RESULT_RETRY_PROMPT,
                user=user_prompt,
            )
        except (ModelResponseError, ValidationError):
            try:
                return self._request(
                    system=system_prompt + MALFORMED_RESULT_REPAIR_PROMPT,
                    user=user_prompt,
                )
            except (ModelResponseError, ValidationError):
                # The observations stay pending. A later bounded wake can retry them without
                # turning a model formatting failure into either a lost signal or a fake idea.
                return None

    @staticmethod
    def _evidence_bound_candidates(
        envelope: CandidateEnvelope,
        observation_map: dict[UUID, Observation],
    ) -> list[Candidate]:
        return [
            candidate
            for candidate in envelope.candidates
            if any(item_id in observation_map for item_id in candidate.supporting_observation_ids)
        ]

    def _monetize(
        self,
        candidate: Candidate,
        observation_map: dict[UUID, Observation],
    ) -> list[Opportunity]:
        evidence = [
            {
                "id": str(item_id),
                "text": observation_map[item_id].text,
                "source": observation_map[item_id].source,
            }
            for item_id in candidate.supporting_observation_ids
            if item_id in observation_map
        ]
        user_prompt = "PROBLEM_HYPOTHESIS:\n" + json.dumps(
            {
                "candidate": candidate.model_dump(mode="json", exclude={"money_models"}),
                "supporting_observations": evidence,
            },
            ensure_ascii=False,
        )
        try:
            envelope = self._money_request(system=MONEY_MODEL_PROMPT, user=user_prompt)
        except (ModelResponseError, ValidationError):
            envelope = None
        if envelope is None or not envelope.money_models:
            try:
                envelope = self._money_request(
                    system=MONEY_MODEL_PROMPT + MONEY_MODEL_REPAIR_PROMPT,
                    user=user_prompt,
                )
            except (ModelResponseError, ValidationError):
                return []
        enriched = candidate.model_copy(update={"money_models": envelope.money_models})
        return self._opportunities(CandidateEnvelope(candidates=[enriched]), observation_map)

    def _opportunities(
        self,
        envelope: CandidateEnvelope,
        observation_map: dict[UUID, Observation],
    ) -> list[Opportunity]:
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
            tags = list(candidate.tags)
            if self.online_only and "online_venture" not in tags:
                tags.append("online_venture")
            problem = Opportunity(
                title=candidate.title,
                problem=candidate.problem,
                customer=candidate.customer,
                proposed_value=candidate.proposed_value,
                evidence=evidence,
                tags=tags,
            )
            money_models = candidate.money_models
            if self.online_only:
                money_models = [
                    item for item in money_models if item.delivery_model in ONLINE_DELIVERY_MODELS
                ]
            if money_models:
                opportunities.extend(expand_problem_opportunity(problem, money_models, limit=3))
            elif not self.online_only:
                # Backward-compatible fallback for old providers/fixtures. New prompts should
                # normally produce explicit money models before an Opportunity is persisted.
                opportunities.append(problem)
        return opportunities

    def _request(self, *, system: str, user: str) -> CandidateEnvelope:
        self.budget.ensure_call_available()
        result = self.provider.complete(system=system, user=user)
        self.budget.record(result)
        return CandidateEnvelope.model_validate_json(result.content)

    def _money_request(self, *, system: str, user: str) -> MoneyModelEnvelope:
        self.budget.ensure_call_available()
        result = self.provider.complete(system=system, user=user)
        self.budget.record(result)
        return MoneyModelEnvelope.model_validate_json(result.content)
