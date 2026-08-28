from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, Field

from helis.agent_spec_domain import (
    AgentMemoryScope,
    AgentSpecBundle,
    AgentToolRequirement,
    ChildAgentSpec,
)
from helis.agent_spec_policy import AgentSpecPolicy
from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.budget import BudgetExceeded, CycleBudget
from helis.domain import AuditEvent, VentureStage
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.venture_architecture_domain import CapabilityImplementation, VentureArchitecture
from helis.venture_architecture_store import VentureArchitectureStore


class AgentOperationalSpecPayload(BaseModel):
    capability_key: str = Field(min_length=2, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    allowed_tools: list[AgentToolRequirement] = Field(default_factory=list, max_length=8)
    memory_scope: AgentMemoryScope = AgentMemoryScope.NONE
    constraints: list[str] = Field(min_length=1, max_length=12)
    stop_conditions: list[str] = Field(min_length=1, max_length=8)
    max_model_turns: int = Field(default=4, ge=1, le=12)
    max_tool_calls_per_run: int = Field(default=4, ge=0, le=20)


class AgentOperationalBundlePayload(BaseModel):
    agents: list[AgentOperationalSpecPayload] = Field(default_factory=list, max_length=6)


@dataclass(slots=True)
class AgentSpecPlanReport:
    opportunity_id: UUID
    bundle: AgentSpecBundle | None = None
    created: bool = False
    model_call_used: bool = False
    blocked_reason: str | None = None
    model_budget_exhausted: bool = False

    @property
    def did_work(self) -> bool:
        return self.created


SYSTEM_PROMPT = """You are HELIS Child Agent Spec Designer.
Translate ONLY the AI-agent capabilities supplied by the approved venture architecture into bounded
operational contracts. You are NOT writing code, prompts, customer messages, credentials, URLs,
deployment config or new business capabilities.

HELIS will copy capability goal, inputs, outputs and success metric directly from the architecture.
You may define only:
- symbolic allowed_tools that are actually required for the capability
- memory_scope: none | venture | customer_thread
- operational constraints
- stop conditions
- max_model_turns (1-12)
- max_tool_calls_per_run (0-20)

Rules:
- Return exactly one entry for every supplied AI-agent capability and no entries for deterministic,
  human or external-service capabilities.
- Never invent a new capability or broaden the supplied capability.
- Tool `action` must be one of that capability's required_actions. It is a future authority
  descriptor, not an authorization grant.
- self_modify is forbidden.
- connector_key and credential_alias are symbolic requirements only. Never output actual URLs,
  tokens, passwords, API keys, account identifiers or secret values.
- If a tool needs credentials, credential_alias is an uppercase symbolic name and the capability
  must already declare credential_access among its required_actions.
- Prefer no memory. Use customer_thread for customer conversational state. A customer-data agent
  may not use venture-wide conversational memory.
- Prefer smaller turn/tool-call limits and explicit stop conditions.
- Every agent remains isolated to its venture; cross-venture memory/data/tool sharing is forbidden.

Return JSON only:
{
  "agents": [
    {
      "capability_key": "existing_ai_capability_key",
      "allowed_tools": [
        {
          "key": "symbolic_tool_key",
          "purpose": "why this capability needs it",
          "action": "research",
          "connector_key": null,
          "credential_alias": null
        }
      ],
      "memory_scope": "none|venture|customer_thread",
      "constraints": ["..."],
      "stop_conditions": ["..."],
      "max_model_turns": 4,
      "max_tool_calls_per_run": 4
    }
  ]
}
"""


def _bundle_hash(
    architecture: VentureArchitecture,
    specs: list[ChildAgentSpec],
) -> str:
    payload = {
        "architecture_id": str(architecture.id),
        "architecture_input_hash": architecture.input_hash,
        "agent_specs": [
            item.model_dump(mode="json")
            for item in sorted(specs, key=lambda spec: spec.capability_key)
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AgentSpecPlanner:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        policy: AgentSpecPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.budget = budget
        self.policy = policy or AgentSpecPolicy()
        self.architectures = VentureArchitectureStore(engine.store)
        self.store = AgentSpecStore(engine.store)

    def plan_if_needed(self, opportunity_id: UUID) -> AgentSpecPlanReport:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            return AgentSpecPlanReport(
                opportunity_id=opportunity_id,
                blocked_reason="opportunity_not_found",
            )
        if opportunity.stage != VentureStage.VALIDATED:
            return AgentSpecPlanReport(
                opportunity_id=opportunity_id,
                blocked_reason="venture_not_validated",
            )

        architecture = self.architectures.latest(opportunity_id)
        if architecture is None:
            return AgentSpecPlanReport(
                opportunity_id=opportunity_id,
                blocked_reason="architecture_missing",
            )
        validation_results = self.engine.store.list_validation_results(opportunity_id)
        if architecture.input_hash != architecture_input_hash(opportunity, validation_results):
            return AgentSpecPlanReport(
                opportunity_id=opportunity_id,
                blocked_reason="architecture_stale",
            )

        existing = self.store.get_for_architecture(architecture.id)
        if existing is not None:
            return AgentSpecPlanReport(
                opportunity_id=opportunity_id,
                bundle=existing,
                created=False,
            )

        targets = [
            item
            for item in architecture.capabilities
            if item.implementation == CapabilityImplementation.AI_AGENT
        ]
        if not targets:
            return self._persist_bundle(architecture, [], model_call_used=False)

        try:
            self.budget.ensure_call_available()
        except BudgetExceeded:
            return AgentSpecPlanReport(
                opportunity_id=opportunity_id,
                model_budget_exhausted=True,
                blocked_reason="model_budget_exhausted",
            )

        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user="AGENT_SPEC_INPUT:\n"
            + json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "architecture_id": str(architecture.id),
                    "architecture_input_hash": architecture.input_hash,
                    "ai_capabilities": [item.model_dump(mode="json") for item in targets],
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = AgentOperationalBundlePayload.model_validate_json(result.content)
        operational = {item.capability_key: item for item in payload.agents}
        if len(operational) != len(payload.agents):
            raise ValueError("model returned duplicate child-agent capability keys")

        specs: list[ChildAgentSpec] = []
        for capability in targets:
            detail = operational.get(capability.key)
            if detail is None:
                raise ValueError(f"model omitted AI-agent capability: {capability.key}")
            specs.append(
                ChildAgentSpec(
                    architecture_id=architecture.id,
                    opportunity_id=opportunity.id,
                    capability_key=capability.key,
                    name=capability.name,
                    goal=capability.goal,
                    inputs=capability.inputs,
                    outputs=capability.outputs,
                    allowed_tools=detail.allowed_tools,
                    memory_scope=detail.memory_scope,
                    constraints=detail.constraints,
                    stop_conditions=detail.stop_conditions,
                    success_metric=capability.success_metric,
                    max_model_turns=detail.max_model_turns,
                    max_tool_calls_per_run=detail.max_tool_calls_per_run,
                    handles_customer_data=capability.handles_customer_data,
                    venture_isolation_required=True,
                )
            )
        if set(operational) != {item.key for item in targets}:
            raise ValueError("model returned non-AI or unknown capability specs")
        return self._persist_bundle(architecture, specs, model_call_used=True)

    def _persist_bundle(
        self,
        architecture: VentureArchitecture,
        specs: list[ChildAgentSpec],
        *,
        model_call_used: bool,
    ) -> AgentSpecPlanReport:
        self.policy.validate(architecture, specs)
        bundle = AgentSpecBundle(
            architecture_id=architecture.id,
            opportunity_id=architecture.opportunity_id,
            architecture_input_hash=architecture.input_hash,
            bundle_hash=_bundle_hash(architecture, specs),
            agent_specs=specs,
        )
        self.store.save(bundle)
        self.engine.store.append_event(
            AuditEvent(
                event_type="venture.agent_specs_planned",
                entity_id=bundle.id,
                data={
                    "opportunity_id": str(bundle.opportunity_id),
                    "architecture_id": str(bundle.architecture_id),
                    "architecture_input_hash": bundle.architecture_input_hash,
                    "bundle_hash": bundle.bundle_hash,
                    "agent_count": len(bundle.agent_specs),
                    "model_call_used": model_call_used,
                },
            )
        )
        return AgentSpecPlanReport(
            opportunity_id=architecture.opportunity_id,
            bundle=bundle,
            created=True,
            model_call_used=model_call_used,
        )
