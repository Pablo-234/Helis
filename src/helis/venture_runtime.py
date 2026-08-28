from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from helis.builder_machine import BuilderMachine, BuildTickReport
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
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
    build: BuildTickReport | None = None


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
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.envelopes = ResourceEnvelopeManager(engine)
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

    def advance(
        self,
        *,
        max_tokens: int = 70_000,
        max_model_cost_cents: float = 20.0,
        validation_cash_cents: float = 0.0,
    ) -> VentureRuntimeReport:
        """Use one shared envelope-backed model budget for validate then build."""
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
        ).tick(self.opportunity_id)
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

    @staticmethod
    def _check_cash_cap(envelope: ResourceEnvelope, requested_cash_cents: float) -> None:
        if requested_cash_cents < 0:
            raise ValueError("validation cash cap cannot be negative")
        if requested_cash_cents > envelope.remaining_cash_cents:
            raise EnvelopeExceeded(
                "validation cash cap exceeds the venture's remaining portfolio envelope"
            )
