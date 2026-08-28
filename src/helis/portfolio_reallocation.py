from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.cash_reservation import CashReservationManager, CashReservationStatus
from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.gtm_feedback import GTMFeedbackRefresher
from helis.portfolio import PortfolioAllocator, PortfolioPlan, PortfolioStore
from helis.portfolio_scheduler import PortfolioScheduler, SchedulerTickReport
from helis.resource_envelope import ResourceEnvelope, ResourceEnvelopeManager


class ReallocationDisposition(StrEnum):
    NO_PLAN = "no_plan"
    ACTIVATED_EXISTING = "activated_existing"
    UNCHANGED = "unchanged"
    DEFERRED_OPEN_COMMITMENT = "deferred_open_commitment"
    REALLOCATED = "reallocated"


class PortfolioReallocationReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    disposition: ReallocationDisposition
    previous_plan_id: UUID | None = None
    new_plan_id: UUID | None = None
    currency: str | None = None
    previous_cash_budget_cents: int = Field(default=0, ge=0)
    cash_consumed_cents: int = Field(default=0, ge=0)
    remaining_cash_cents: int = Field(default=0, ge=0)
    previous_model_call_budget: int = Field(default=0, ge=0)
    model_calls_consumed: int = Field(default=0, ge=0)
    remaining_model_calls: int = Field(default=0, ge=0)
    activated_envelopes: int = Field(default=0, ge=0)
    reason: str = Field(min_length=2, max_length=500)
    created_at: datetime = Field(default_factory=utc_now)


class PortfolioReallocationStore:
    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS portfolio_reallocations (
                    id TEXT PRIMARY KEY,
                    disposition TEXT NOT NULL,
                    previous_plan_id TEXT,
                    new_plan_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_portfolio_reallocations_created
                    ON portfolio_reallocations(created_at);
                """
            )

    def save(self, report: PortfolioReallocationReport) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO portfolio_reallocations "
                "(id, disposition, previous_plan_id, new_plan_id, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(report.id),
                    report.disposition.value,
                    str(report.previous_plan_id) if report.previous_plan_id else None,
                    str(report.new_plan_id) if report.new_plan_id else None,
                    report.model_dump_json(),
                    report.created_at.isoformat(),
                ),
            )

    def latest(self) -> PortfolioReallocationReport | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM portfolio_reallocations ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return PortfolioReallocationReport.model_validate_json(row["payload"]) if row else None


class PortfolioReallocator:
    """Rolls remaining treasury into a new plan without restoring consumed resources."""

    def __init__(self, engine: HelisEngine) -> None:
        self.engine = engine
        self.portfolio = PortfolioStore(engine)
        self.allocator = PortfolioAllocator(engine)
        # Envelope tables must exist before cash-reservation triggers are initialized.
        self.envelopes = ResourceEnvelopeManager(engine)
        self.cash = CashReservationManager(engine)
        self.state = PortfolioReallocationStore(engine)

    def reconcile(self) -> PortfolioReallocationReport:
        previous = self.portfolio.latest()
        if previous is None:
            return self._record(
                PortfolioReallocationReport(
                    disposition=ReallocationDisposition.NO_PLAN,
                    reason="no portfolio plan exists",
                )
            )

        plan_envelopes = [
            item for item in self.envelopes.list() if item.plan_id == previous.id
        ]
        if not plan_envelopes:
            if not previous.allocations:
                return self._record(
                    self._report(
                        previous,
                        disposition=ReallocationDisposition.UNCHANGED,
                        new_plan_id=previous.id,
                        reason="latest plan has no funded venture allocations",
                    )
                )
            try:
                activated = self.envelopes.activate(previous)
            except sqlite3.IntegrityError as exc:
                if not self._is_open_commitment_error(exc):
                    raise
                return self._record(
                    self._report(
                        previous,
                        disposition=ReallocationDisposition.DEFERRED_OPEN_COMMITMENT,
                        new_plan_id=previous.id,
                        reason="a concurrent open cash commitment blocked plan activation",
                    )
                )
            return self._record(
                self._report(
                    previous,
                    disposition=ReallocationDisposition.ACTIVATED_EXISTING,
                    new_plan_id=previous.id,
                    activated_envelopes=len(activated),
                    reason="latest funded plan had not been activated yet",
                )
            )

        open_commitments = self._open_commitments(plan_envelopes)
        cash_consumed = sum(item.cash_consumed_cents for item in plan_envelopes)
        model_calls_consumed = sum(item.model_calls_consumed for item in plan_envelopes)
        if open_commitments:
            return self._record(
                self._report(
                    previous,
                    disposition=ReallocationDisposition.DEFERRED_OPEN_COMMITMENT,
                    cash_consumed=cash_consumed,
                    model_calls_consumed=model_calls_consumed,
                    reason=(
                        f"{open_commitments} open cash commitment(s) must settle or release "
                        "before envelope rollover"
                    ),
                )
            )

        remaining_cash = max(0, previous.budget.cash_cents - cash_consumed)
        remaining_calls = max(0, previous.budget.model_calls - model_calls_consumed)
        remaining_budget = previous.budget.model_copy(
            update={
                "cash_cents": remaining_cash,
                "model_calls": remaining_calls,
            }
        )

        proposed = self.allocator.plan(remaining_budget)
        if proposed.id == previous.id:
            return self._record(
                self._report(
                    previous,
                    disposition=ReallocationDisposition.UNCHANGED,
                    new_plan_id=previous.id,
                    cash_consumed=cash_consumed,
                    model_calls_consumed=model_calls_consumed,
                    reason="portfolio inputs and remaining treasury are unchanged",
                )
            )

        try:
            activated = self.envelopes.activate(proposed)
        except sqlite3.IntegrityError as exc:
            if not self._is_open_commitment_error(exc):
                raise
            self._discard_unactivated_plan(proposed, previous)
            return self._record(
                self._report(
                    previous,
                    disposition=ReallocationDisposition.DEFERRED_OPEN_COMMITMENT,
                    new_plan_id=previous.id,
                    cash_consumed=cash_consumed,
                    model_calls_consumed=model_calls_consumed,
                    reason="a concurrent open cash commitment blocked envelope rollover",
                )
            )

        return self._record(
            self._report(
                previous,
                disposition=ReallocationDisposition.REALLOCATED,
                new_plan_id=proposed.id,
                cash_consumed=cash_consumed,
                model_calls_consumed=model_calls_consumed,
                activated_envelopes=len(activated),
                reason="portfolio state changed; remaining treasury was reallocated",
            )
        )

    def _open_commitments(self, envelopes: list[ResourceEnvelope]) -> int:
        return sum(
            item.status == CashReservationStatus.RESERVED
            for envelope in envelopes
            for item in self.cash.list(envelope.id)
        )

    @staticmethod
    def _is_open_commitment_error(exc: sqlite3.IntegrityError) -> bool:
        message = str(exc).lower()
        return "reservation" in message or "open cash commitment" in message

    def _discard_unactivated_plan(
        self,
        proposed: PortfolioPlan,
        previous: PortfolioPlan,
    ) -> None:
        """Do not leave an unactivated plan as latest after a commitment race."""
        with self.engine.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute(
                "SELECT id FROM portfolio_plans ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            has_envelopes = db.execute(
                "SELECT 1 FROM resource_envelopes WHERE plan_id = ? LIMIT 1",
                (str(proposed.id),),
            ).fetchone()
            if latest and latest["id"] == str(proposed.id) and has_envelopes is None:
                db.execute("DELETE FROM portfolio_plans WHERE id = ?", (str(proposed.id),))

        if self.portfolio.latest() is None:
            self.portfolio.save(previous)

    def _report(
        self,
        previous: PortfolioPlan,
        *,
        disposition: ReallocationDisposition,
        reason: str,
        new_plan_id: UUID | None = None,
        cash_consumed: int = 0,
        model_calls_consumed: int = 0,
        activated_envelopes: int = 0,
    ) -> PortfolioReallocationReport:
        return PortfolioReallocationReport(
            disposition=disposition,
            previous_plan_id=previous.id,
            new_plan_id=new_plan_id,
            currency=previous.budget.currency,
            previous_cash_budget_cents=previous.budget.cash_cents,
            cash_consumed_cents=cash_consumed,
            remaining_cash_cents=max(0, previous.budget.cash_cents - cash_consumed),
            previous_model_call_budget=previous.budget.model_calls,
            model_calls_consumed=model_calls_consumed,
            remaining_model_calls=max(0, previous.budget.model_calls - model_calls_consumed),
            activated_envelopes=activated_envelopes,
            reason=reason,
        )

    def _record(self, report: PortfolioReallocationReport) -> PortfolioReallocationReport:
        self.state.save(report)
        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.reallocation",
                entity_id=report.id,
                data={
                    "disposition": report.disposition.value,
                    "previous_plan_id": (
                        str(report.previous_plan_id) if report.previous_plan_id else None
                    ),
                    "new_plan_id": str(report.new_plan_id) if report.new_plan_id else None,
                    "cash_consumed_cents": report.cash_consumed_cents,
                    "remaining_cash_cents": report.remaining_cash_cents,
                    "model_calls_consumed": report.model_calls_consumed,
                    "remaining_model_calls": report.remaining_model_calls,
                    "activated_envelopes": report.activated_envelopes,
                    "reason": report.reason,
                },
            )
        )
        return report


class ReallocatingPortfolioControlLoop:
    """Wake/tick entrypoint: refresh GTM, reconcile capital, then run the bounded scheduler."""

    def __init__(
        self,
        engine: HelisEngine,
        scheduler: PortfolioScheduler,
    ) -> None:
        self.feedback = GTMFeedbackRefresher(engine)
        self.reallocator = PortfolioReallocator(engine)
        self.scheduler = scheduler

    def tick(self, *, max_advances: int) -> SchedulerTickReport:
        self.feedback.refresh_all()
        self.reallocator.reconcile()
        return self.scheduler.tick(max_advances=max_advances)
