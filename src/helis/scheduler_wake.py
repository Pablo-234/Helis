from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.portfolio_scheduler import SchedulerTickReport


class WakeDisposition(StrEnum):
    RAN = "ran"
    NOT_DUE = "not_due"
    LEASE_HELD = "lease_held"
    FAILED = "failed"


class WakePolicy(BaseModel):
    minimum_interval_seconds: int = Field(default=900, ge=0, le=86_400)
    lease_seconds: int = Field(default=600, ge=1, le=86_400)
    max_advances: int = Field(default=1, ge=1, le=20)


class SchedulerWakeResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    disposition: WakeDisposition
    owner_id: UUID | None = None
    scheduler_report_id: UUID | None = None
    reason: str = Field(min_length=2, max_length=500)
    attempted_at: datetime
    completed_at: datetime | None = None


class SchedulerTicker(Protocol):
    def tick(self, *, max_advances: int) -> SchedulerTickReport: ...


class WakeLeaseLost(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_time(value: str | None) -> datetime | None:
    return _utc(datetime.fromisoformat(value)) if value else None


class SchedulerWakeStore:
    lease_name = "portfolio-scheduler"

    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduler_wake_state (
                    name TEXT PRIMARY KEY,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_attempt_at TEXT,
                    last_completed_at TEXT,
                    last_report_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scheduler_wake_results (
                    id TEXT PRIMARY KEY,
                    disposition TEXT NOT NULL,
                    owner_id TEXT,
                    scheduler_report_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scheduler_wake_results_created
                    ON scheduler_wake_results(created_at);
                """
            )

    def latest_result(self) -> SchedulerWakeResult | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM scheduler_wake_results ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return SchedulerWakeResult.model_validate_json(row["payload"]) if row else None

    def save_result(self, result: SchedulerWakeResult) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO scheduler_wake_results "
                "(id, disposition, owner_id, scheduler_report_id, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(result.id),
                    result.disposition.value,
                    str(result.owner_id) if result.owner_id else None,
                    str(result.scheduler_report_id) if result.scheduler_report_id else None,
                    result.model_dump_json(),
                    result.attempted_at.isoformat(),
                ),
            )

    def acquire(
        self,
        policy: WakePolicy,
        *,
        owner_id: UUID,
        now: datetime,
    ) -> tuple[WakeDisposition | None, str]:
        current = _utc(now)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM scheduler_wake_state WHERE name = ?",
                (self.lease_name,),
            ).fetchone()
            lease_owner = row["lease_owner"] if row else None
            lease_expires_at = _parse_time(row["lease_expires_at"]) if row else None
            last_attempt_at = _parse_time(row["last_attempt_at"]) if row else None

            if lease_owner and lease_expires_at and lease_expires_at > current:
                return WakeDisposition.LEASE_HELD, f"lease held until {lease_expires_at.isoformat()}"

            if last_attempt_at is not None:
                next_due = last_attempt_at + timedelta(seconds=policy.minimum_interval_seconds)
                if current < next_due:
                    return WakeDisposition.NOT_DUE, f"next wake due at {next_due.isoformat()}"

            lease_expires = current + timedelta(seconds=policy.lease_seconds)
            if row is None:
                db.execute(
                    "INSERT INTO scheduler_wake_state "
                    "(name, lease_owner, lease_expires_at, last_attempt_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        self.lease_name,
                        str(owner_id),
                        lease_expires.isoformat(),
                        current.isoformat(),
                        current.isoformat(),
                    ),
                )
            else:
                db.execute(
                    "UPDATE scheduler_wake_state SET lease_owner = ?, lease_expires_at = ?, "
                    "last_attempt_at = ?, updated_at = ? WHERE name = ?",
                    (
                        str(owner_id),
                        lease_expires.isoformat(),
                        current.isoformat(),
                        current.isoformat(),
                        self.lease_name,
                    ),
                )
        return None, "lease_acquired"

    def finish_owned(
        self,
        *,
        owner_id: UUID,
        now: datetime,
        scheduler_report_id: UUID | None,
        mark_completed: bool,
    ) -> None:
        current = _utc(now)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT lease_owner FROM scheduler_wake_state WHERE name = ?",
                (self.lease_name,),
            ).fetchone()
            if row is None or row["lease_owner"] != str(owner_id):
                raise WakeLeaseLost("scheduler wake lease is no longer owned by this worker")

            if mark_completed:
                db.execute(
                    "UPDATE scheduler_wake_state SET lease_owner = NULL, lease_expires_at = NULL, "
                    "last_completed_at = ?, last_report_id = ?, updated_at = ? WHERE name = ?",
                    (
                        current.isoformat(),
                        str(scheduler_report_id) if scheduler_report_id else None,
                        current.isoformat(),
                        self.lease_name,
                    ),
                )
            else:
                db.execute(
                    "UPDATE scheduler_wake_state SET lease_owner = NULL, lease_expires_at = NULL, "
                    "updated_at = ? WHERE name = ?",
                    (current.isoformat(), self.lease_name),
                )


class SchedulerWakeController:
    """Cron-safe wake gate: due check + expiring single-worker lease + bounded scheduler tick."""

    def __init__(
        self,
        engine: HelisEngine,
        ticker: SchedulerTicker,
    ) -> None:
        self.engine = engine
        self.ticker = ticker
        self.state = SchedulerWakeStore(engine)

    def wake(
        self,
        policy: WakePolicy | None = None,
        *,
        now: datetime | None = None,
    ) -> SchedulerWakeResult:
        wake_policy = policy or WakePolicy()
        attempted_at = _utc(now or utc_now())
        owner_id = uuid4()
        disposition, reason = self.state.acquire(
            wake_policy,
            owner_id=owner_id,
            now=attempted_at,
        )
        if disposition is not None:
            result = SchedulerWakeResult(
                disposition=disposition,
                reason=reason,
                attempted_at=attempted_at,
            )
            self._record(result)
            return result

        try:
            report = self.ticker.tick(max_advances=wake_policy.max_advances)
        except Exception as exc:  # noqa: BLE001 -- wake boundary isolates scheduler/runtime failures
            finished_at = _utc(now or utc_now())
            try:
                self.state.finish_owned(
                    owner_id=owner_id,
                    now=finished_at,
                    scheduler_report_id=None,
                    mark_completed=False,
                )
                reason = f"{type(exc).__name__}: {exc}"
            except WakeLeaseLost as lease_exc:
                reason = f"{type(exc).__name__}: {exc}; {lease_exc}"
            result = SchedulerWakeResult(
                disposition=WakeDisposition.FAILED,
                owner_id=owner_id,
                reason=reason,
                attempted_at=attempted_at,
                completed_at=finished_at,
            )
            self._record(result)
            return result

        finished_at = _utc(now or utc_now())
        try:
            self.state.finish_owned(
                owner_id=owner_id,
                now=finished_at,
                scheduler_report_id=report.id,
                mark_completed=True,
            )
            disposition = WakeDisposition.RAN
            reason = "scheduler_tick_completed"
        except WakeLeaseLost as exc:
            disposition = WakeDisposition.FAILED
            reason = str(exc)

        result = SchedulerWakeResult(
            disposition=disposition,
            owner_id=owner_id,
            scheduler_report_id=report.id,
            reason=reason,
            attempted_at=attempted_at,
            completed_at=finished_at,
        )
        self._record(result)
        return result

    def _record(self, result: SchedulerWakeResult) -> None:
        self.state.save_result(result)
        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.scheduler_wake",
                entity_id=result.id,
                data={
                    "disposition": result.disposition.value,
                    "owner_id": str(result.owner_id) if result.owner_id else None,
                    "scheduler_report_id": (
                        str(result.scheduler_report_id) if result.scheduler_report_id else None
                    ),
                    "reason": result.reason,
                },
            )
        )
