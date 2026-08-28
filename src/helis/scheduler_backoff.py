from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field

from helis.domain import AuditEvent
from helis.engine import HelisEngine


class SchedulerBackoff(BaseModel):
    opportunity_id: UUID
    reason: str = Field(min_length=2, max_length=200)
    fingerprint: str = Field(min_length=64, max_length=64)
    consecutive_noops: int = Field(default=1, ge=1)
    next_eligible_at: datetime
    updated_at: datetime


class AdaptiveBackoffPolicy:
    """Deterministic cooldowns for repeated venture no-op outcomes."""

    _BASE_SECONDS = {
        "approval_backlog": 15 * 60,
        "result_backlog": 30 * 60,
        "contact_gateway_missing": 60 * 60,
        "prospect_gateway_missing": 60 * 60,
        "no_model_capacity": 60 * 60,
        "market_scan_no_new_signal": 30 * 60,
    }
    _MAX_SECONDS = {
        "approval_backlog": 6 * 60 * 60,
        "result_backlog": 12 * 60 * 60,
        "contact_gateway_missing": 24 * 60 * 60,
        "prospect_gateway_missing": 24 * 60 * 60,
        "no_model_capacity": 12 * 60 * 60,
        "market_scan_no_new_signal": 6 * 60 * 60,
    }

    def supports(self, reason: str) -> bool:
        return reason in self._BASE_SECONDS

    def delay_seconds(self, reason: str, consecutive_noops: int) -> int:
        if not self.supports(reason):
            return 0
        exponent = min(20, max(0, consecutive_noops - 1))
        return min(
            self._BASE_SECONDS[reason] * (2**exponent),
            self._MAX_SECONDS[reason],
        )


class SchedulerBackoffStore:
    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS venture_scheduler_backoffs (
                    opportunity_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    consecutive_noops INTEGER NOT NULL,
                    next_eligible_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_venture_scheduler_backoffs_next
                    ON venture_scheduler_backoffs(next_eligible_at);
                """
            )

    def get(self, opportunity_id: UUID) -> SchedulerBackoff | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM venture_scheduler_backoffs WHERE opportunity_id = ?",
                (str(opportunity_id),),
            ).fetchone()
        return SchedulerBackoff.model_validate_json(row["payload"]) if row else None

    def save(self, item: SchedulerBackoff) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO venture_scheduler_backoffs "
                "(opportunity_id, reason, fingerprint, consecutive_noops, "
                "next_eligible_at, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(item.opportunity_id),
                    item.reason,
                    item.fingerprint,
                    item.consecutive_noops,
                    item.next_eligible_at.isoformat(),
                    item.model_dump_json(),
                    item.updated_at.isoformat(),
                ),
            )

    def clear(self, opportunity_id: UUID) -> None:
        with self.store.connect() as db:
            db.execute(
                "DELETE FROM venture_scheduler_backoffs WHERE opportunity_id = ?",
                (str(opportunity_id),),
            )


class AdaptiveSchedulerBackoff:
    def __init__(
        self,
        engine: HelisEngine,
        policy: AdaptiveBackoffPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.state = SchedulerBackoffStore(engine)
        self.policy = policy or AdaptiveBackoffPolicy()

    def record_noop(
        self,
        opportunity_id: UUID,
        *,
        reason: str,
        fingerprint: str,
        now: datetime | None = None,
    ) -> SchedulerBackoff | None:
        if not self.policy.supports(reason):
            self.clear(opportunity_id)
            return None
        current = (now or datetime.now(UTC)).astimezone(UTC)
        existing = self.state.get(opportunity_id)
        consecutive = 1
        if (
            existing is not None
            and existing.reason == reason
            and existing.fingerprint == fingerprint
        ):
            consecutive = existing.consecutive_noops + 1
        delay = self.policy.delay_seconds(reason, consecutive)
        item = SchedulerBackoff(
            opportunity_id=opportunity_id,
            reason=reason,
            fingerprint=fingerprint,
            consecutive_noops=consecutive,
            next_eligible_at=current + timedelta(seconds=delay),
            updated_at=current,
        )
        self.state.save(item)
        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.scheduler_backoff",
                entity_id=opportunity_id,
                data={
                    "reason": reason,
                    "fingerprint": fingerprint,
                    "consecutive_noops": consecutive,
                    "delay_seconds": delay,
                    "next_eligible_at": item.next_eligible_at.isoformat(),
                },
            )
        )
        return item

    def skip_reason(
        self,
        opportunity_id: UUID,
        *,
        fingerprint: str,
        now: datetime | None = None,
    ) -> str | None:
        item = self.state.get(opportunity_id)
        if item is None:
            return None
        if item.fingerprint != fingerprint:
            self.clear(opportunity_id)
            return None
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if current >= item.next_eligible_at.astimezone(UTC):
            return None
        return f"backoff:{item.reason}:until={item.next_eligible_at.astimezone(UTC).isoformat()}"

    def clear(self, opportunity_id: UUID) -> None:
        existing = self.state.get(opportunity_id)
        if existing is None:
            return
        self.state.clear(opportunity_id)
        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.scheduler_backoff_reset",
                entity_id=opportunity_id,
                data={
                    "previous_reason": existing.reason,
                    "previous_fingerprint": existing.fingerprint,
                    "previous_consecutive_noops": existing.consecutive_noops,
                },
            )
        )
