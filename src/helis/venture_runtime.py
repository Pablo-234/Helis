from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from helis.agent_spec_planner import AgentSpecPlanner, AgentSpecPlanReport
from helis.bot_architect import ArchitecturePlanReport, BotArchitect
from helis.builder_machine import BuilderMachine, BuildTickReport
from helis.cash_reservation import CashReservationManager
from helis.contact_gateway import ContactGateway
from helis.domain import VentureStage
from helis.engine import HelisEngine
from helis.gtm_lifecycle import gtm_is_active
from helis.gtm_runtime import GTMRuntime, GTMTickReport
from helis.model_provider import ModelProvider
from helis.prospect_gateway import ProspectGateway
from helis.resource_envelope import (
    EnvelopeCycleBudget,
    EnvelopeExceeded,
    EnvelopeStatus,
    ResourceEnvelope,
    ResourceEnvelopeManager,
)
from helis.validation_execution import ValidationBudget
from helis.validation_gateway import ApprovedValidationGateway
from helis.validation_machine import ValidationMachine, ValidationTickReport


@dataclass(slots=True)
class VentureRuntimeReport:
    envelope: ResourceEnvelope
    budget: EnvelopeCycleBudget
    validation: ValidationTickReport | None = None
    architecture: ArchitecturePlanReport | None = None
    agent_specs: AgentSpecPlanReport | None = None
    build: BuildTickReport | None = None
    gtm: GTMTickReport | None = None

    @property
    def did_work(self) -> bool:
        if self.gtm is not None:
            return self.gtm.did_work
        if self.agent_specs is not None and self.build is None:
            return self.agent_specs.did_work
        if self.architecture is not None and self.build is None:
            return self.architecture.did_work
        return True


class VentureRuntime:
    """Runs venture-specific work under one persistent portfolio resource envelope."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        envelope_id: UUID,
        *,
        workspace_root: str | Path = ".helis/workspaces",
        validation_gateway: ApprovedValidationGateway | None = None,
        prospect_gateway: ProspectGateway | None = None,
        contact_gateway: ContactGateway | None = None,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.envelopes = ResourceEnvelopeManager(engine)
        self.cash = CashReservationManager(engine)
        envelope = self.envelopes.get(envelope_id)
        if envelope is None:
            raise ValueError(f"resource envelope not found: {envelope_id}")
        if envelope.status != EnvelopeStatus.ACTIVE:
            raise EnvelopeExceeded(f"resource envelope is {envelope.status.value}")
        opportunity = engine.store.get_opportunity(envelope.opportunity_id)
        if opportunity is None:
            raise ValueError(f"envelope venture not found: {envelope.opportunity_id}")
        self.envelope_id = envelope_id
        self.opportunity_id = envelope.opportunity_id
        self.workspace_root = Path(workspace_root)
        self.validation_gateway = validation_gateway
        self.prospect_gateway = prospect_gateway
        self.contact_gateway = contact_gateway

    def validate(
        self,
        *,
        max_tokens: int = 35_000,
        max_model_cost_cents: float = 10.0,
        validation_cash_cents: float = 0.0,
    ) -> VentureRuntimeReport:
        envelope = self._active_envelope()
        self._check_cash_cap(envelope, validation_cash_cents)
        budget = self.envelopes.model_budget(
            envelope.id,
            max_tokens=max_tokens,
            max_model_cost_cents=max_model_cost_cents,
        )
        report = ValidationMachine(
            self.engine,
            self.provider,
            budget,
            validation_budget=ValidationBudget(
                max_executions=1,
                max_cash_cents=validation_cash_cents,
            ),
            external_gateway=self.validation_gateway,
            cash_envelope_id=envelope.id,
        ).tick(self.opportunity_id)
        return VentureRuntimeReport(
            envelope=self._require_envelope(),
            budget=budget,
            validation=report,
        )

    def build(
        self,
        *,
        max_tokens: int = 45_000,
        max_model_cost_cents: float = 15.0,
    ) -> VentureRuntimeReport:
        envelope = self._active_envelope()
        budget = self.envelopes.model_budget(
            envelope.id,
            max_tokens=max_tokens,
            max_model_cost_cents=max_model_cost_cents,
        )
        report = BuilderMachine(
            self.engine,
            self.provider,
            budget,
            workspace_root=self.workspace_root,
        ).tick(self.opportunity_id)
        return VentureRuntimeReport(
            envelope=self._require_envelope(),
            budget=budget,
            build=report,
        )

    def market(
        self,
        *,
        max_tokens: int = 45_000,
        max_model_cost_cents: float = 15.0,
    ) -> VentureRuntimeReport:
        envelope = self._active_envelope()
        budget = self.envelopes.model_budget(
            envelope.id,
            max_tokens=max_tokens,
            max_model_cost_cents=max_model_cost_cents,
        )
        report = GTMRuntime(
            self.engine,
            self.provider,
            budget,
            prospect_gateway=self.prospect_gateway,
            contact_gateway=self.contact_gateway,
        ).tick(self.opportunity_id)
        return VentureRuntimeReport(
            envelope=self._require_envelope(),
            budget=budget,
            gtm=report,
        )

    def advance(
        self,
        *,
        max_tokens: int = 70_000,
        max_model_cost_cents: float = 20.0,
        validation_cash_cents: float = 0.0,
    ) -> VentureRuntimeReport:
        """Advance the venture's current lifecycle phase under one persistent envelope."""
        opportunity = self.engine.store.get_opportunity(self.opportunity_id)
        if opportunity is None:
            raise ValueError(f"venture not found: {self.opportunity_id}")
        if gtm_is_active(opportunity.stage):
            return self.market(
                max_tokens=max_tokens,
                max_model_cost_cents=max_model_cost_cents,
            )

        envelope = self._active_envelope()
        self._check_cash_cap(envelope, validation_cash_cents)
        budget = self.envelopes.model_budget(
            envelope.id,
            max_tokens=max_tokens,
            max_model_cost_cents=max_model_cost_cents,
        )
        validation = ValidationMachine(
            self.engine,
            self.provider,
            budget,
            validation_budget=ValidationBudget(
                max_executions=1,
                max_cash_cents=validation_cash_cents,
            ),
            external_gateway=self.validation_gateway,
            cash_envelope_id=envelope.id,
        ).tick(self.opportunity_id)

        current = self.engine.store.get_opportunity(self.opportunity_id)
        architecture: ArchitecturePlanReport | None = None
        agent_specs: AgentSpecPlanReport | None = None
        if (
            current is not None
            and current.stage == VentureStage.VALIDATED
            and current.business_model is not None
        ):
            architecture = BotArchitect(self.engine, self.provider, budget).plan_if_needed(
                self.opportunity_id
            )
            if architecture.created or architecture.model_budget_exhausted:
                return VentureRuntimeReport(
                    envelope=self._require_envelope(),
                    budget=budget,
                    validation=validation,
                    architecture=architecture,
                )
            if architecture.blocked_reason is not None:
                return VentureRuntimeReport(
                    envelope=self._require_envelope(),
                    budget=budget,
                    validation=validation,
                    architecture=architecture,
                )

            agent_specs = AgentSpecPlanner(self.engine, self.provider, budget).plan_if_needed(
                self.opportunity_id
            )
            if agent_specs.model_budget_exhausted or agent_specs.blocked_reason is not None:
                return VentureRuntimeReport(
                    envelope=self._require_envelope(),
                    budget=budget,
                    validation=validation,
                    architecture=architecture,
                    agent_specs=agent_specs,
                )
            if agent_specs.created and agent_specs.model_call_used:
                return VentureRuntimeReport(
                    envelope=self._require_envelope(),
                    budget=budget,
                    validation=validation,
                    architecture=architecture,
                    agent_specs=agent_specs,
                )

        build = BuilderMachine(
            self.engine,
            self.provider,
            budget,
            workspace_root=self.workspace_root,
        ).tick(self.opportunity_id)
        return VentureRuntimeReport(
            envelope=self._require_envelope(),
            budget=budget,
            validation=validation,
            architecture=architecture,
            agent_specs=agent_specs,
            build=build,
        )

    def _active_envelope(self) -> ResourceEnvelope:
        envelope = self._require_envelope()
        if envelope.status != EnvelopeStatus.ACTIVE:
            raise EnvelopeExceeded(f"resource envelope is {envelope.status.value}")
        return envelope

    def _require_envelope(self) -> ResourceEnvelope:
        envelope = self.envelopes.get(self.envelope_id)
        if envelope is None:
            raise ValueError(f"resource envelope not found: {self.envelope_id}")
        return envelope

    def _check_cash_cap(self, envelope: ResourceEnvelope, requested_cash_cents: float) -> None:
        if requested_cash_cents < 0:
            raise ValueError("validation cash cap cannot be negative")
        available = self.cash.available_cash(envelope.id)
        if requested_cash_cents > available:
            raise EnvelopeExceeded(
                "validation cash cap exceeds available cash after open reservations"
            )
