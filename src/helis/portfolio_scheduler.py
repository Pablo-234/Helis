from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.cash_reservation import CashReservationManager, CashReservationStatus
from helis.domain import AuditEvent, ExperimentRunStatus, VentureStage, utc_now
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.portfolio import PortfolioStore
from helis.resource_envelope import EnvelopeStatus, ResourceEnvelope, ResourceEnvelopeManager
from helis.validation_gateway import ApprovedValidationGateway
from helis.venture_runtime import VentureRuntime


class SchedulerDisposition(StrEnum):
    ADVANCED = "advanced"
    SKIPPED = "skipped"
    FAILED = "failed"


class SchedulerItem(BaseModel):
    envelope_id: UUID
    opportunity_id: UUID
    priority_score: float = Field(default=0, ge=0)
    disposition: SchedulerDisposition
    reason: str = Field(min_length=2, max_length=500)
    model_calls_before: int = Field(default=0, ge=0)
    model_calls_after: int = Field(default=0, ge=0)
    available_cash_before: int = Field(default=0, ge=0)
    available_cash_after: int = Field(default=0, ge=0)


class SchedulerTickReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID | None = None
    max_advances: int = Field(default=1, ge=1, le=20)
    attempted_advances: int = Field(default=0, ge=0)
    items: list[SchedulerItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def advanced(self) -> int:
        return sum(item.disposition == SchedulerDisposition.ADVANCED for item in self.items)

    @property
    def skipped(self) -> int:
        return sum(item.disposition == SchedulerDisposition.SKIPPED for item in self.items)

    @property
    def failed(self) -> int:
        return sum(item.disposition == SchedulerDisposition.FAILED for item in self.items)


class VentureAdvancer(Protocol):
    def advance(self, *, validation_cash_cents: float = 0.0): ...


RuntimeFactory = Callable[[UUID], VentureAdvancer]


class SchedulerStore:
    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS portfolio_scheduler_ticks (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_portfolio_scheduler_ticks_created
                    ON portfolio_scheduler_ticks(created_at);
                """
            )

    def save(self, report: SchedulerTickReport) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO portfolio_scheduler_ticks (id, plan_id, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    str(report.id),
                    str(report.plan_id) if report.plan_id else None,
                    report.model_dump_json(),
                    report.created_at.isoformat(),
                ),
            )

    def latest(self) -> SchedulerTickReport | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM portfolio_scheduler_ticks ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return SchedulerTickReport.model_validate_json(row["payload"]) if row else None


class PortfolioScheduler:
    """Selects bounded venture work from the latest active portfolio envelopes."""

    _ACTIONABLE_STAGES = frozenset(
        {
            VentureStage.EVALUATED,
            VentureStage.VALIDATING,
            VentureStage.VALIDATED,
            VentureStage.BUILDING,
        }
    )
    _BLOCKING_VALIDATION_STATUSES = frozenset(
        {
            ExperimentRunStatus.WAITING_APPROVAL,
            ExperimentRunStatus.WAITING_RESULT,
            ExperimentRunStatus.RUNNING,
        }
    )

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        *,
        workspace_root: str | Path = ".helis/workspaces",
        validation_gateway: ApprovedValidationGateway | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.workspace_root = Path(workspace_root)
        self.validation_gateway = validation_gateway
        self.envelopes = ResourceEnvelopeManager(engine)
        self.cash = CashReservationManager(engine)
        self.portfolio = PortfolioStore(engine)
        self.state = SchedulerStore(engine)
        self.runtime_factory = runtime_factory or self._default_runtime

    def tick(self, *, max_advances: int = 1) -> SchedulerTickReport:
        if not 1 <= max_advances <= 20:
            raise ValueError("max_advances must be between 1 and 20")
        plan = self.portfolio.latest()
        if plan is None:
            report = SchedulerTickReport(max_advances=max_advances)
            self._save(report)
            return report

        priorities = {
            allocation.opportunity_id: allocation.priority_score
            for allocation in plan.allocations
        }
        active = self.envelopes.list(status=EnvelopeStatus.ACTIVE)
        active.sort(
            key=lambda envelope: (
                -priorities.get(envelope.opportunity_id, 0.0),
                str(envelope.opportunity_id),
            )
        )

        items: list[SchedulerItem] = []
        attempts = 0
        for envelope in active:
            priority = priorities.get(envelope.opportunity_id, 0.0)
            cash_before = self.cash.available_cash(envelope.id)
            reason = self._skip_reason(envelope, plan.id)
            if reason is not None:
                items.append(
                    self._item(
                        envelope,
                        priority,
                        SchedulerDisposition.SKIPPED,
                        reason,
                        cash_before,
                    )
                )
                continue
            if attempts >= max_advances:
                items.append(
                    self._item(
                        envelope,
                        priority,
                        SchedulerDisposition.SKIPPED,
                        "tick_advance_cap",
                        cash_before,
                    )
                )
                continue

            attempts += 1
            try:
                self.runtime_factory(envelope.id).advance(
                    validation_cash_cents=float(cash_before),
                )
            except Exception as exc:  # noqa: BLE001 -- isolate one venture from the portfolio loop
                items.append(
                    self._item(
                        envelope,
                        priority,
                        SchedulerDisposition.FAILED,
                        f"{type(exc).__name__}: {exc}",
                        cash_before,
                    )
                )
                continue

            refreshed = self.envelopes.get(envelope.id) or envelope
            cash_after = self.cash.available_cash(envelope.id)
            items.append(
                SchedulerItem(
                    envelope_id=envelope.id,
                    opportunity_id=envelope.opportunity_id,
                    priority_score=priority,
                    disposition=SchedulerDisposition.ADVANCED,
                    reason="venture_runtime_advanced",
                    model_calls_before=envelope.model_calls_consumed,
                    model_calls_after=refreshed.model_calls_consumed,
                    available_cash_before=cash_before,
                    available_cash_after=cash_after,
                )
            )

        report = SchedulerTickReport(
            plan_id=plan.id,
            max_advances=max_advances,
            attempted_advances=attempts,
            items=items,
        )
        self._save(report)
        return report

    def _skip_reason(self, envelope: ResourceEnvelope, latest_plan_id: UUID) -> str | None:
        if envelope.plan_id != latest_plan_id:
            return "stale_active_envelope"
        opportunity = self.engine.store.get_opportunity(envelope.opportunity_id)
        if opportunity is None:
            return "venture_missing"
        if opportunity.stage not in self._ACTIONABLE_STAGES:
            return f"stage_not_actionable:{opportunity.stage.value}"

        reservations = self.cash.list(envelope.id)
        if any(item.status == CashReservationStatus.RESERVED for item in reservations):
            return "open_cash_commitment"

        runs = self.engine.store.list_experiment_runs(opportunity_id=opportunity.id)
        blocking = next(
            (run for run in runs if run.status in self._BLOCKING_VALIDATION_STATUSES),
            None,
        )
        if blocking is not None:
            return f"validation_{blocking.status.value}"

        if envelope.remaining_model_calls <= 0:
            return "no_model_capacity"
        return None

    def _item(
        self,
        envelope: ResourceEnvelope,
        priority: float,
        disposition: SchedulerDisposition,
        reason: str,
        cash_before: int,
    ) -> SchedulerItem:
        refreshed = self.envelopes.get(envelope.id) or envelope
        return SchedulerItem(
            envelope_id=envelope.id,
            opportunity_id=envelope.opportunity_id,
            priority_score=priority,
            disposition=disposition,
            reason=reason,
            model_calls_before=envelope.model_calls_consumed,
            model_calls_after=refreshed.model_calls_consumed,
            available_cash_before=cash_before,
            available_cash_after=self.cash.available_cash(envelope.id),
        )

    def _default_runtime(self, envelope_id: UUID) -> VentureRuntime:
        return VentureRuntime(
            self.engine,
            self.provider,
            envelope_id,
            workspace_root=self.workspace_root,
            validation_gateway=self.validation_gateway,
        )

    def _save(self, report: SchedulerTickReport) -> None:
        self.state.save(report)
        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.scheduler_tick",
                entity_id=report.id,
                data={
                    "plan_id": str(report.plan_id) if report.plan_id else None,
                    "max_advances": report.max_advances,
                    "attempted_advances": report.attempted_advances,
                    "advanced": report.advanced,
                    "skipped": report.skipped,
                    "failed": report.failed,
                    "items": [
                        {
                            "envelope_id": str(item.envelope_id),
                            "opportunity_id": str(item.opportunity_id),
                            "disposition": item.disposition.value,
                            "reason": item.reason,
                        }
                        for item in report.items
                    ],
                },
            )
        )
