from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from helis.agent_spec_domain import AgentMemoryScope, AgentSpecBundle, ChildAgentSpec
from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.child_agent_domain import ChildAgentArtifact
from helis.child_agent_factory import ChildAgentFactory
from helis.domain import Opportunity, ValidationOutcome, ValidationResult, VentureStage
from helis.engine import HelisEngine
from helis.venture_architecture_domain import (
    CapabilityImplementation,
    CapabilityNode,
    VentureArchitecture,
)
from helis.venture_architecture_store import VentureArchitectureStore


SERVICE_INTAKE_TAG = "starter:service_intake_v1"


@dataclass(slots=True)
class StarterAgentReport:
    opportunity: Opportunity
    artifact: ChildAgentArtifact
    created: bool


def create_service_intake_starter(
    engine: HelisEngine,
    *,
    workspace_root: str | Path = ".helis/ventures",
) -> StarterAgentReport:
    """Create one persistent, useful local child agent for real inbound-service triage.

    This is an operator-requested starter utility, not a claim of market validation. It exists so
    the real child-agent queue/runtime can be exercised on real work before autonomous ventures
    have produced their own child agents.
    """

    existing = next(
        (item for item in engine.store.list_opportunities() if SERVICE_INTAKE_TAG in item.tags),
        None,
    )
    factory = ChildAgentFactory(engine, workspace_root=workspace_root)
    if existing is not None:
        report = factory.materialize_if_needed(existing.id)
        if report.blocked_reason is not None:
            raise RuntimeError(f"starter agent is incomplete: {report.blocked_reason}")
        if len(report.artifacts) != 1:
            raise RuntimeError("starter agent must materialize exactly one child agent")
        return StarterAgentReport(existing, report.artifacts[0], created=False)

    opportunity = Opportunity(
        title="Service intake worker starter",
        problem=(
            "Incoming service inquiries arrive as unstructured text and need consistent triage "
            "before a person or downstream automation can act on them."
        ),
        customer="operator-provided service workflow",
        proposed_value=(
            "Convert each supplied inquiry into an actionable intake summary without inventing "
            "facts or contacting the customer."
        ),
        tags=[SERVICE_INTAKE_TAG, "operator_utility_not_market_validated"],
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    validation = ValidationResult(
        run_id=uuid4(),
        experiment_id=uuid4(),
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.POSITIVE,
        confidence=1.0,
        summary=(
            "Operator explicitly requested a useful local starter worker. This validates only the "
            "local operational need and does not assert external market demand."
        ),
        metrics={"operator_requested_utility": 1.0},
        source="operator_starter_request",
    )
    engine.store.save_validation_result(validation)
    snapshot = architecture_input_hash(opportunity, [validation])
    capability = CapabilityNode(
        key="triage_service_inquiry",
        name="Triage service inquiry",
        goal=(
            "Turn one supplied inbound service inquiry into a concise actionable intake analysis "
            "that separates known facts from missing information."
        ),
        implementation=CapabilityImplementation.AI_AGENT,
        inputs=["one inbound inquiry record"],
        outputs=["actionable intake analysis"],
        required_actions=[],
        success_metric=(
            "analysis identifies request, known facts, missing facts and next questions without "
            "inventing customer information"
        ),
        rationale=(
            "Natural-language intake requires bounded interpretation but no external tools for the "
            "first useful local worker."
        ),
        handles_customer_data=True,
        venture_isolation_required=True,
    )
    architecture = VentureArchitecture(
        opportunity_id=opportunity.id,
        input_hash=snapshot,
        capabilities=[capability],
        architecture_assumptions=[
            "This starter performs local intake analysis only and does not contact customers.",
            "It is an operator utility, not evidence of a validated market venture.",
        ],
    )
    VentureArchitectureStore(engine.store).save(architecture)
    spec = ChildAgentSpec(
        architecture_id=architecture.id,
        opportunity_id=opportunity.id,
        capability_key=capability.key,
        name=capability.name,
        goal=capability.goal,
        inputs=capability.inputs,
        outputs=capability.outputs,
        allowed_tools=[],
        memory_scope=AgentMemoryScope.NONE,
        constraints=[
            "Use only facts present in the supplied record.",
            "Never invent a budget, deadline, service type, customer identity or preference.",
            "Never claim that a customer was contacted, booked, priced or promised anything.",
            "Return a compact machine-readable JSON object as the output string.",
            (
                "The output object must contain: summary, known_facts, missing_information, "
                "questions_to_ask, readiness, recommended_next_step."
            ),
            "readiness must be one of ready, needs_clarification, not_a_service_inquiry.",
        ],
        stop_conditions=[
            "The supplied record has been classified and an actionable intake object is complete."
        ],
        success_metric=capability.success_metric,
        max_model_turns=2,
        max_tool_calls_per_run=0,
        handles_customer_data=True,
        venture_isolation_required=True,
    )
    semantic = json.dumps(
        spec.model_dump(mode="json", exclude={"id"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    bundle = AgentSpecBundle(
        architecture_id=architecture.id,
        opportunity_id=opportunity.id,
        architecture_input_hash=snapshot,
        bundle_hash=hashlib.sha256(semantic).hexdigest(),
        agent_specs=[spec],
    )
    AgentSpecStore(engine.store).save(bundle)
    report = factory.materialize_if_needed(opportunity.id)
    if report.blocked_reason is not None:
        raise RuntimeError(f"starter factory failed: {report.blocked_reason}")
    if len(report.artifacts) != 1:
        raise RuntimeError("starter agent must materialize exactly one child agent")
    return StarterAgentReport(opportunity, report.artifacts[0], created=True)
