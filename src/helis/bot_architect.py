from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, Field

from helis.budget import BudgetExceeded, CycleBudget
from helis.domain import AuditEvent, Opportunity, ValidationResult, VentureStage
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.venture_architecture_domain import CapabilityNode, VentureArchitecture
from helis.venture_architecture_policy import VentureArchitecturePolicy
from helis.venture_architecture_store import VentureArchitectureStore


class ArchitecturePayload(BaseModel):
    capabilities: list[CapabilityNode] = Field(min_length=1, max_length=12)
    owner_responsibilities: list[str] = Field(default_factory=list, max_length=8)
    architecture_assumptions: list[str] = Field(default_factory=list, max_length=8)


@dataclass(slots=True)
class ArchitecturePlanReport:
    opportunity_id: UUID
    architecture: VentureArchitecture | None = None
    created: bool = False
    blocked_reason: str | None = None
    model_budget_exhausted: bool = False

    @property
    def did_work(self) -> bool:
        return self.created


SYSTEM_PROMPT = """You are HELIS Bot Architect.
Design the MINIMUM capability graph needed to operate one validated venture business model.
You are designing an operating system for a child venture, NOT modifying HELIS itself and NOT
writing code, prompts, shell commands, credentials, deployment config or customer messages.

Choose implementation for each capability:
- deterministic_automation: rules/calculation/state transitions that do not need model judgment
- ai_agent: genuinely ambiguous language/reasoning work where an LLM-like agent adds value
- human: work that should intentionally remain human, including high-stakes judgment when needed
- external_service: a third-party capability that should be integrated rather than rebuilt

Rules:
- Prefer fewer capabilities and fewer AI agents. Do not create one agent per tiny task.
- Prefer deterministic automation over AI_AGENT for rules-based work.
- A child venture may NEVER require self_modify.
- required_actions are DESCRIPTORS OF FUTURE AUTHORITY NEEDS, not permissions. Sensitive actions
  such as external_contact, publication, spend or credential_access remain separately gated later.
- Every capability must remain venture-isolated. Never share customer data/credentials across ventures.
- Use the validated business_model and validation_results. Treat unvalidated economic fields as
  hypotheses even if they are present on the opportunity.
- Dependencies must form a DAG and reference capability keys from the same response.
- Return no more than 12 capabilities and no more than 6 ai_agent capabilities.
- Do not force an AI agent when the venture can operate with deterministic automation or humans.

Allowed required_actions: research, network_read, file_write, sandbox_execution, network_write,
external_contact, publication, spend, credential_access. self_modify is forbidden.

Return JSON only:
{
  "capabilities": [
    {
      "key": "snake_case_key",
      "name": "...",
      "goal": "...",
      "implementation": "deterministic_automation|ai_agent|human|external_service",
      "inputs": ["..."],
      "outputs": ["..."],
      "depends_on": ["other_key"],
      "required_actions": ["research"],
      "success_metric": "...",
      "rationale": "...",
      "handles_customer_data": false,
      "venture_isolation_required": true
    }
  ],
  "owner_responsibilities": ["only unavoidable owner duties"],
  "architecture_assumptions": ["claims this architecture still depends on"]
}
"""


def architecture_input_hash(
    opportunity: Opportunity,
    validation_results: list[ValidationResult],
) -> str:
    payload = {
        "opportunity": opportunity.model_dump(mode="json"),
        "validation_results": [
            item.model_dump(mode="json")
            for item in sorted(validation_results, key=lambda result: str(result.id))
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class BotArchitect:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        policy: VentureArchitecturePolicy | None = None,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.budget = budget
        self.policy = policy or VentureArchitecturePolicy()
        self.store = VentureArchitectureStore(engine.store)

    def plan_if_needed(self, opportunity_id: UUID) -> ArchitecturePlanReport:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            return ArchitecturePlanReport(
                opportunity_id=opportunity_id,
                blocked_reason="opportunity_not_found",
            )
        if opportunity.stage != VentureStage.VALIDATED:
            return ArchitecturePlanReport(
                opportunity_id=opportunity_id,
                blocked_reason="venture_not_validated",
            )
        if opportunity.business_model is None:
            return ArchitecturePlanReport(
                opportunity_id=opportunity_id,
                blocked_reason="business_model_missing",
            )

        results = self.engine.store.list_validation_results(opportunity_id)
        if not results:
            return ArchitecturePlanReport(
                opportunity_id=opportunity_id,
                blocked_reason="validation_results_missing",
            )
        input_hash = architecture_input_hash(opportunity, results)
        existing = self.store.get_for_snapshot(opportunity.id, input_hash)
        if existing is not None:
            return ArchitecturePlanReport(
                opportunity_id=opportunity.id,
                architecture=existing,
                created=False,
            )

        try:
            self.budget.ensure_call_available()
        except BudgetExceeded:
            return ArchitecturePlanReport(
                opportunity_id=opportunity.id,
                model_budget_exhausted=True,
                blocked_reason="model_budget_exhausted",
            )

        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user="ARCHITECTURE_INPUT:\n"
            + json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "validation_results": [item.model_dump(mode="json") for item in results],
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = ArchitecturePayload.model_validate_json(result.content)
        self.policy.validate(payload.capabilities)

        architecture = VentureArchitecture(
            opportunity_id=opportunity.id,
            input_hash=input_hash,
            capabilities=payload.capabilities,
            owner_responsibilities=payload.owner_responsibilities,
            architecture_assumptions=payload.architecture_assumptions,
        )
        self.store.save(architecture)
        self.engine.store.append_event(
            AuditEvent(
                event_type="venture.architecture_planned",
                entity_id=architecture.id,
                data={
                    "opportunity_id": str(opportunity.id),
                    "input_hash": input_hash,
                    "capability_count": len(architecture.capabilities),
                    "ai_agent_count": sum(
                        item.implementation.value == "ai_agent"
                        for item in architecture.capabilities
                    ),
                },
            )
        )
        return ArchitecturePlanReport(
            opportunity_id=opportunity.id,
            architecture=architecture,
            created=True,
        )
