from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from helis.domain import utc_now
from helis.host_scheduler import HostSchedulerInspector
from helis.live_gateway_factory import live_gateways_from_env
from helis.live_readiness import LiveReadinessInspector, ReadinessLevel
from helis.model_provider import OpenAICompatibleProvider
from helis.validation_gateway import ApprovedValidationGateway
from helis.vercel_gateway import VercelCliPreviewGateway


class LiveActivationCheck(BaseModel):
    key: str = Field(min_length=2, max_length=80)
    label: str = Field(min_length=2, max_length=160)
    level: ReadinessLevel
    detail: str = Field(min_length=1, max_length=2000)
    required_for_activation: bool = True


class LiveActivationReport(BaseModel):
    checks: list[LiveActivationCheck]
    activation_ready: bool
    inspected_at: datetime = Field(default_factory=utc_now)

    @property
    def blocking(self) -> list[LiveActivationCheck]:
        return [
            item
            for item in self.checks
            if item.required_for_activation and item.level == ReadinessLevel.BLOCKED
        ]


class LiveActivationInspector:
    """Fail-closed, configuration-only check for the complete recurring revenue path.

    The optional model probe is restricted by ``LiveReadinessInspector`` to localhost metadata.
    External gateways are constructed and described, but never called.
    """

    def __init__(
        self,
        provider: OpenAICompatibleProvider,
        *,
        config: str | Path,
        db: str | Path,
        workspace_root: str | Path,
        self_improvement_root: str | Path,
        host_scheduler: HostSchedulerInspector | None = None,
        which: Callable[[str], str | None] | None = None,
    ) -> None:
        self.readiness = LiveReadinessInspector(
            provider,
            config=config,
            db=db,
            workspace_root=workspace_root,
            self_improvement_root=self_improvement_root,
            host_scheduler=host_scheduler,
        )
        self.which = which or shutil.which

    def inspect(
        self,
        *,
        probe_model: bool = False,
        require_schedule: bool = False,
    ) -> LiveActivationReport:
        baseline = self.readiness.inspect(probe_model=probe_model)
        checks: list[LiveActivationCheck] = []
        for item in baseline.checks:
            if item.key == "gateways":
                continue
            required = item.required_for_pilot or (
                item.key == "timers" and require_schedule
            )
            level = item.level
            if item.key == "timers" and require_schedule and level != ReadinessLevel.READY:
                level = ReadinessLevel.BLOCKED
            checks.append(
                LiveActivationCheck(
                    key=item.key,
                    label=item.label,
                    level=level,
                    detail=item.detail,
                    required_for_activation=required,
                )
            )

        checks.extend(self._gateway_checks())
        checks.append(
            LiveActivationCheck(
                key="policy_gates",
                label="Persisted approval gates",
                level=ReadinessLevel.READY,
                detail=(
                    "publication, first contact and checkout creation remain separately approved"
                ),
            )
        )
        ready = not any(
            item.required_for_activation and item.level == ReadinessLevel.BLOCKED
            for item in checks
        )
        return LiveActivationReport(checks=checks, activation_ready=ready)

    def _gateway_checks(self) -> list[LiveActivationCheck]:
        try:
            selected = live_gateways_from_env()
            validation = ApprovedValidationGateway.from_env()
        except ValueError as exc:
            return [
                LiveActivationCheck(
                    key="gateway_configuration",
                    label="Live adapter configuration",
                    level=ReadinessLevel.BLOCKED,
                    detail=f"invalid configuration: {exc}",
                )
            ]

        slots = [
            ("validation", "External validation", validation),
            ("preview", "Preview publication", selected.preview),
            ("prospect", "Prospect discovery", selected.prospect),
            ("contact", "First contact", selected.contact),
            ("contact_result", "Reply observation", selected.contact_result),
            ("commerce", "Checkout and payment", selected.commerce),
        ]
        return [self._gateway_check(key, label, gateway) for key, label, gateway in slots]

    def _gateway_check(self, key: str, label: str, gateway) -> LiveActivationCheck:
        if gateway is None:
            return LiveActivationCheck(
                key=f"gateway_{key}",
                label=label,
                level=ReadinessLevel.BLOCKED,
                detail=f"{key} adapter is missing or incomplete",
            )
        if key == "preview" and isinstance(gateway, VercelCliPreviewGateway):
            executable = self.which(gateway.cli)
            if executable is None:
                return LiveActivationCheck(
                    key="gateway_preview",
                    label=label,
                    level=ReadinessLevel.BLOCKED,
                    detail=f"Vercel CLI {gateway.cli!r} is not available on PATH",
                )
        return LiveActivationCheck(
            key=f"gateway_{key}",
            label=label,
            level=ReadinessLevel.READY,
            detail=f"{gateway.name} @ {gateway.safe_destination}",
        )
