from __future__ import annotations

import sqlite3
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from helis.cash_reservation import CashReservationManager, CashReservationStatus
from helis.domain import AuditEvent
from helis.engine import HelisEngine
from helis.portfolio import PortfolioAllocator, PortfolioPlan, PortfolioStore
from helis.resource_envelope import EnvelopeStatus, ResourceEnvelopeManager


class RebalanceDisposition(StrEnum):
    REBALANCED = "rebalanced"
    UNCHANGED = "unchanged"
    BLOCKED = "blocked"
    NO_PLAN = "no_plan"


class PortfolioRebalanceResult(BaseModel):
    disposition: RebalanceDisposition
    previous_plan_id: UUID | None = None
    plan_id: UUID | None = None
    activated_envelopes: int = Field(default=0, ge=0)
    reason: str = Field(min_length=2, max_length=500)


class SchedulerTicker(Protocol):
    def tick(self, *, max_advances: int): ...


class PortfolioRebalancer:
    """Recomputes the existing portfolio budget against fresh venture/GTM economics."""

    def __init__(self, engine: HelisEngine) -> None:
        self.engine = engine
        self.portfolio = PortfolioStore(engine)
        self.allocator = PortfolioAllocator(engine)
        self.envelopes = ResourceEnvelopeManager(engine)
        self.cash = CashReservationManager(engine)

    def rebalance(self) -> PortfolioRebalanceResult:
        previous = self.portfolio.latest()
        if previous is None:
            return self._record(
                PortfolioRebalanceResult(
                    disposition=RebalanceDisposition.NO_PLAN,
                    reason="no portfolio budget exists yet",
                )
            )

        active = self.envelopes.list(status=EnvelopeStatus.ACTIVE)
        if self._has_open_commitments(active):
            return self._record(
                PortfolioRebalanceResult(
                    disposition=RebalanceDisposition.BLOCKED,
                    previous_plan_id=previous.id,
                    plan_id=previous.id,
                    reason="open cash commitment prevents portfolio supersession",
                )
            )

        candidate = self.allocator.plan(previous.budget)
        already_active = active and all(item.plan_id == candidate.id for item in active)
        if candidate.id == previous.id and already_active:
            return self._record(
                PortfolioRebalanceResult(
                    disposition=RebalanceDisposition.UNCHANGED,
                    previous_plan_id=previous.id,
                    plan_id=candidate.id,
                    activated_envelopes=len(active),
                    reason="portfolio snapshot unchanged",
                )
            )

        try:
            activated = self.envelopes.activate(candidate)
        except sqlite3.IntegrityError:
            if candidate.id != previous.id:
                self._discard_unactivated(candidate, previous)
            return self._record(
                PortfolioRebalanceResult(
                    disposition=RebalanceDisposition.BLOCKED,
                    previous_plan_id=previous.id,
                    plan_id=previous.id,
                    activated_envelopes=len(active),
                    reason="portfolio activation blocked by a concurrent open commitment",
                )
            )

        disposition = (
            RebalanceDisposition.REBALANCED
            if candidate.id != previous.id
            else RebalanceDisposition.UNCHANGED
        )
        return self._record(
            PortfolioRebalanceResult(
                disposition=disposition,
                previous_plan_id=previous.id,
                plan_id=candidate.id,
                activated_envelopes=len(activated),
                reason=(
                    "fresh venture economics changed portfolio allocation"
                    if disposition == RebalanceDisposition.REBALANCED
                    else "latest plan was activated"
                ),
            )
        )

    def _has_open_commitments(self, active: list) -> bool:
        for envelope in active:
            if any(
                item.status == CashReservationStatus.RESERVED
                for item in self.cash.list(envelope.id)
            ):
                return True
        return False

    def _discard_unactivated(self, candidate: PortfolioPlan, previous: PortfolioPlan) -> None:
        """Restore the prior latest plan if a just-created plan could not be activated."""
        with self.engine.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute(
                "SELECT id FROM portfolio_plans ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            has_envelopes = db.execute(
                "SELECT 1 FROM resource_envelopes WHERE plan_id = ? LIMIT 1",
                (str(candidate.id),),
            ).fetchone()
            if latest and latest["id"] == str(candidate.id) and has_envelopes is None:
                db.execute("DELETE FROM portfolio_plans WHERE id = ?", (str(candidate.id),))
        if self.portfolio.latest() is None:
            self.portfolio.save(previous)

    def _record(self, result: PortfolioRebalanceResult) -> PortfolioRebalanceResult:
        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.rebalance",
                entity_id=result.plan_id or result.previous_plan_id,
                data=result.model_dump(mode="json"),
            )
        )
        return result


class RebalancingSchedulerTicker:
    """Refresh allocation first, then execute the ordinary bounded scheduler tick."""

    def __init__(
        self,
        engine: HelisEngine,
        ticker: SchedulerTicker,
    ) -> None:
        self.rebalancer = PortfolioRebalancer(engine)
        self.ticker = ticker
        self.last_rebalance: PortfolioRebalanceResult | None = None

    def tick(self, *, max_advances: int):
        self.last_rebalance = self.rebalancer.rebalance()
        return self.ticker.tick(max_advances=max_advances)
