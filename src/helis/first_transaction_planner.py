from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, Field

from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.budget import BudgetExceeded, CycleBudget
from helis.domain import AuditEvent, Opportunity, ValidationResult, VentureStage
from helis.engine import HelisEngine
from helis.first_transaction_domain import (
    AcquisitionChannel,
    FirstTransactionPlan,
    PaymentRail,
)
from helis.first_transaction_policy import (
    FirstTransactionPolicy,
    TransactionExecutionContext,
    required_transaction_actions,
    transaction_execution_blockers,
)
from helis.first_transaction_store import FirstTransactionStore
from helis.model_provider import ModelProvider
from helis.venture_architecture_store import VentureArchitectureStore


class FirstTransactionPayload(BaseModel):
    offer_name: str = Field(min_length=3, max_length=160)
    offer_summary: str = Field(min_length=10, max_length=1200)
    price_cents: int = Field(ge=1, le=100_000_000)
    acquisition_channel: AcquisitionChannel
    prospect_profile: str = Field(min_length=10, max_length=1200)
    acquisition_strategy: str = Field(min_length=10, max_length=1600)
    required_sales_asset: str = Field(min_length=10, max_length=1600)
    fulfillment_promise: str = Field(min_length=10, max_length=1600)
    fulfillment_steps: list[str] = Field(min_length=1, max_length=8)
    payment_rail: PaymentRail
    owner_responsibilities: list[str] = Field(default_factory=list, max_length=8)
    launch_assumptions: list[str] = Field(default_factory=list, max_length=8)


@dataclass(slots=True)
class FirstTransactionPlanReport:
    opportunity_id: UUID
    plan: FirstTransactionPlan | None = None
    created: bool = False
    blocked_reason: str | None = None
    model_budget_exhausted: bool = False

    @property
    def did_work(self) -> bool:
        return self.created


SYSTEM_PROMPT = """You are HELIS First Transaction Planner.
Design the SHORTEST credible path from one validated online venture to its first real revenue event.
You are not writing outreach copy and you are not executing any external action.

Hard rules:
- Use the persisted business model, validation results and venture operating architecture.
- Do not change payer, currency or billing unit; HELIS will inherit those outside your response.
- price_cents must remain inside the supplied persisted pricing hypothesis and must be positive.
- Do not invent customers, traction, testimonials, revenue, guarantees, certifications or proof.
- Prefer the smallest sellable offer and the smallest sales asset that can support one transaction.
- Prefer a low-cost acquisition route that HELIS can actually operate with the supplied execution
  context. Do not choose a route merely because it sounds scalable.
- Do not choose paid advertising; v1 deliberately focuses on reversible zero/low-cash first sales.
- fulfillment_promise must be deliverable by the supplied capability graph. Do not promise work the
  venture has no capability to perform.
- owner_responsibilities should contain only unavoidable human work, not tasks that could already be
  handled by the supplied capabilities.
- launch_assumptions are unresolved claims, never evidence.

Acquisition channels:
- b2b_direct_outreach: narrow public-business prospect search plus first contact
- partnership_outreach: contact a business/distribution partner
- marketplace_listing: publish the offer on a marketplace
- community_launch: publish a relevant, non-spammy public offer in a suitable community
- content_inbound: publish a useful public asset intended to attract inbound buyers

Payment rails:
- manual_invoice
- checkout_link
- marketplace_checkout (only with marketplace_listing)
- platform_payout

Return JSON only with:
offer_name, offer_summary, price_cents, acquisition_channel, prospect_profile,
acquisition_strategy, required_sales_asset, fulfillment_promise, fulfillment_steps,
payment_rail, owner_responsibilities, launch_assumptions.
"""


def first_transaction_input_hash(
    opportunity: Opportunity,
    validation_results: list[ValidationResult],
    architecture_id: UUID,
    architecture_input_hash_value: str,
    agent_spec_bundle_id: UUID,
    agent_spec_bundle_hash: str,
    execution_context: TransactionExecutionContext,
) -> str:
    payload = {
        "opportunity": opportunity.model_dump(mode="json"),
        "validation_results": [
            item.model_dump(mode="json")
            for item in sorted(validation_results, key=lambda result: str(result.id))
        ],
        "architecture_id": str(architecture_id),
        "architecture_input_hash": architecture_input_hash_value,
        "agent_spec_bundle_id": str(agent_spec_bundle_id),
        "agent_spec_bundle_hash": agent_spec_bundle_hash,
        "execution_context": {
            "prospect_gateway_available": execution_context.prospect_gateway_available,
            "contact_gateway_available": execution_context.contact_gateway_available,
            "publication_channel_available": execution_context.publication_channel_available,
            "payment_gateway_available": execution_context.payment_gateway_available,
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FirstTransactionPlanner:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        *,
        execution_context: TransactionExecutionContext | None = None,
        policy: FirstTransactionPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.budget = budget
        self.execution_context = execution_context or TransactionExecutionContext()
        self.policy = policy or FirstTransactionPolicy()
        self.architectures = VentureArchitectureStore(engine.store)
        self.specs = AgentSpecStore(engine.store)
        self.store = FirstTransactionStore(engine.store)

    def plan_if_needed(self, opportunity_id: UUID) -> FirstTransactionPlanReport:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            return FirstTransactionPlanReport(opportunity_id, blocked_reason="opportunity_not_found")
        if opportunity.stage != VentureStage.VALIDATED:
            return FirstTransactionPlanReport(opportunity_id, blocked_reason="venture_not_validated")
        business_model = opportunity.business_model
        if business_model is None:
            return FirstTransactionPlanReport(opportunity_id, blocked_reason="business_model_missing")

        results = self.engine.store.list_validation_results(opportunity_id)
        if not results:
            return FirstTransactionPlanReport(opportunity_id, blocked_reason="validation_results_missing")
        architecture = self.architectures.latest(opportunity_id)
        if architecture is None:
            return FirstTransactionPlanReport(opportunity_id, blocked_reason="architecture_missing")
        current_architecture_hash = architecture_input_hash(opportunity, results)
        if architecture.input_hash != current_architecture_hash:
            return FirstTransactionPlanReport(opportunity_id, blocked_reason="architecture_stale")
        bundle = self.specs.latest(opportunity_id)
        if bundle is None:
            return FirstTransactionPlanReport(opportunity_id, blocked_reason="agent_specs_missing")
        if (
            bundle.architecture_id != architecture.id
            or bundle.architecture_input_hash != architecture.input_hash
        ):
            return FirstTransactionPlanReport(opportunity_id, blocked_reason="agent_specs_stale")

        input_hash = first_transaction_input_hash(
            opportunity,
            results,
            architecture.id,
            architecture.input_hash,
            bundle.id,
            bundle.bundle_hash,
            self.execution_context,
        )
        existing = self.store.get_for_snapshot(opportunity_id, input_hash)
        if existing is not None:
            return FirstTransactionPlanReport(opportunity_id, plan=existing)

        try:
            self.budget.ensure_call_available()
        except BudgetExceeded:
            return FirstTransactionPlanReport(
                opportunity_id,
                blocked_reason="model_budget_exhausted",
                model_budget_exhausted=True,
            )

        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user="FIRST_TRANSACTION_INPUT:\n"
            + json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "validation_results": [item.model_dump(mode="json") for item in results],
                    "architecture": architecture.model_dump(mode="json"),
                    "agent_spec_bundle": bundle.model_dump(mode="json"),
                    "execution_context": {
                        "prospect_gateway_available": self.execution_context.prospect_gateway_available,
                        "contact_gateway_available": self.execution_context.contact_gateway_available,
                        "publication_channel_available": self.execution_context.publication_channel_available,
                        "payment_gateway_available": self.execution_context.payment_gateway_available,
                    },
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = FirstTransactionPayload.model_validate_json(result.content)
        self.policy.validate_price(opportunity, payload.price_cents)
        self.policy.validate_route(payload.acquisition_channel, payload.payment_rail)

        required_actions = required_transaction_actions(
            payload.acquisition_channel,
            payload.payment_rail,
        )
        execution_blockers = transaction_execution_blockers(
            payload.acquisition_channel,
            payload.payment_rail,
            self.execution_context,
        )
        plan = FirstTransactionPlan(
            opportunity_id=opportunity.id,
            architecture_id=architecture.id,
            agent_spec_bundle_id=bundle.id,
            input_hash=input_hash,
            payer=business_model.payer,
            offer_name=payload.offer_name,
            offer_summary=payload.offer_summary,
            price_cents=payload.price_cents,
            currency=business_model.pricing.currency,
            billing_unit=business_model.pricing.unit,
            acquisition_channel=payload.acquisition_channel,
            prospect_profile=payload.prospect_profile,
            acquisition_strategy=payload.acquisition_strategy,
            required_sales_asset=payload.required_sales_asset,
            fulfillment_promise=payload.fulfillment_promise,
            fulfillment_steps=payload.fulfillment_steps,
            payment_rail=payload.payment_rail,
            first_transaction_success=(
                "one real payer produces an attributed revenue event of at least "
                f"{payload.price_cents} {business_model.pricing.currency} cents for this exact offer"
            ),
            required_actions=required_actions,
            execution_blockers=execution_blockers,
            owner_responsibilities=payload.owner_responsibilities,
            launch_assumptions=payload.launch_assumptions,
        )
        self.store.save(plan)
        self.engine.store.append_event(
            AuditEvent(
                event_type="venture.first_transaction_planned",
                entity_id=plan.id,
                data={
                    "opportunity_id": str(opportunity.id),
                    "input_hash": input_hash,
                    "price_cents": plan.price_cents,
                    "currency": plan.currency,
                    "acquisition_channel": plan.acquisition_channel.value,
                    "payment_rail": plan.payment_rail.value,
                    "execution_ready": plan.execution_ready,
                    "execution_blockers": plan.execution_blockers,
                    "required_actions": [item.value for item in plan.required_actions],
                },
            )
        )
        return FirstTransactionPlanReport(
            opportunity_id=opportunity.id,
            plan=plan,
            created=True,
        )
