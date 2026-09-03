from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.cycle import HelisCycle
from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.source_registry import RegistryScanResult


class DiscoveryWakeDisposition(StrEnum):
    RAN = "ran"
    NOT_DUE = "not_due"
    LEASE_HELD = "lease_held"
    FAILED = "failed"


class DiscoveryWakePolicy(BaseModel):
    minimum_interval_seconds: int = Field(default=3600, ge=0, le=86_400)
    lease_seconds: int = Field(default=900, ge=1, le=86_400)
    observation_limit: int = Field(default=100, ge=1, le=1000)
    candidate_limit: int = Field(default=5, ge=1, le=50)
    max_model_calls: int = Field(default=8, ge=0, le=100)
    max_tokens: int = Field(default=40_000, ge=0, le=2_000_000)
    max_cost_cents: float = Field(default=25.0, ge=0, le=100_000)


class DiscoveryWakeResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    disposition: DiscoveryWakeDisposition
    owner_id: UUID | None = None
    reason: str = Field(min_length=2, max_length=1000)
    attempted_at: datetime
    completed_at: datetime | None = None
    observations_fetched: int = Field(default=0, ge=0)
    observations_new: int = Field(default=0, ge=0)
    source_failures: int = Field(default=0, ge=0)
    observations_used: int = Field(default=0, ge=0)
    candidates_discovered: int = Field(default=0, ge=0)
    candidates_evaluated: int = Field(default=0, ge=0)
    experiments_planned: int = Field(default=0, ge=0)
    budget_exhausted: bool = False
    model_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    cost_cents: float = Field(default=0, ge=0)

    @property
    def did_work(self) -> bool:
        return any(
            (
                self.observations_new,
                self.observations_used,
                self.candidates_discovered,
                self.candidates_evaluated,
                self.experiments_planned,
            )
        )


class DiscoveryScanner(Protocol):
    def scan(self) -> RegistryScanResult: ...


class DiscoveryRuntime:
    """One bounded market scan plus one resumable HELIS brain cycle."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        scanner_factory: Callable[[], DiscoveryScanner],
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.scanner_factory = scanner_factory

    def tick(self, policy: DiscoveryWakePolicy) -> DiscoveryWakeResult:
        attempted_at = utc_now()
        scan = self.scanner_factory().scan()
        before = self._observation_count()
        for observation in scan.observations:
            self.engine.observe(observation)
        after = self._observation_count()

        budget = CycleBudget(
            max_model_calls=policy.max_model_calls,
            max_tokens=policy.max_tokens,
            max_cost_cents=policy.max_cost_cents,
        )
        cycle = HelisCycle(self.engine, self.provider, budget, online_only=True).run(
            observation_limit=policy.observation_limit,
            candidate_limit=policy.candidate_limit,
        )
        completed_at = utc_now()
        if cycle.candidates_discovered:
            reason = "discovery_cycle_completed"
        elif cycle.budget_exhausted:
            reason = "discovery_budget_exhausted_before_candidate"
        elif cycle.observations_used:
            reason = (
                "scout_returned_no_candidates_after_replay"
                if cycle.observations_replayed
                else "scout_returned_no_candidates"
            )
        else:
            reason = "no_observations_to_analyze"
        return DiscoveryWakeResult(
            disposition=DiscoveryWakeDisposition.RAN,
            reason=reason,
            attempted_at=attempted_at,
            completed_at=completed_at,
            observations_fetched=len(scan.observations),
            observations_new=max(0, after - before),
            source_failures=len(scan.failures),
            observations_used=cycle.observations_used,
            candidates_discovered=cycle.candidates_discovered,
            candidates_evaluated=cycle.candidates_evaluated,
            experiments_planned=cycle.experiments_planned,
            budget_exhausted=cycle.budget_exhausted,
            model_calls=budget.model_calls,
            tokens=budget.tokens,
            cost_cents=budget.cost_cents,
        )

    def _observation_count(self) -> int:
        with self.engine.store.connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM observations").fetchone()
        return int(row["count"] if row else 0)


class DiscoveryWakeLeaseLost(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_time(value: str | None) -> datetime | None:
    return _utc(datetime.fromisoformat(value)) if value else None


def _safe_failure_reason(exc: Exception) -> str:
    message = " ".join(str(exc).split()) or "no error detail"
    reason = f"{type(exc).__name__}: {message}"
    return reason if len(reason) <= 1000 else reason[:997] + "..."


class DiscoveryWakeStore:
    lease_name = "market-discovery"

    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS discovery_wake_state (
                    name TEXT PRIMARY KEY,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_attempt_at TEXT,
                    last_completed_at TEXT,
                    last_result_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovery_wake_results (
                    id TEXT PRIMARY KEY,
                    disposition TEXT NOT NULL,
                    owner_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_discovery_wake_results_created
                    ON discovery_wake_results(created_at);
                """
            )

    def latest_result(self) -> DiscoveryWakeResult | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM discovery_wake_results ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return DiscoveryWakeResult.model_validate_json(row["payload"]) if row else None

    def save_result(self, result: DiscoveryWakeResult) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO discovery_wake_results "
                "(id, disposition, owner_id, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(result.id),
                    result.disposition.value,
                    str(result.owner_id) if result.owner_id else None,
                    result.model_dump_json(),
                    result.attempted_at.isoformat(),
                ),
            )

    def acquire(
        self,
        policy: DiscoveryWakePolicy,
        *,
        owner_id: UUID,
        now: datetime,
    ) -> tuple[DiscoveryWakeDisposition | None, str]:
        current = _utc(now)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM discovery_wake_state WHERE name = ?",
                (self.lease_name,),
            ).fetchone()
            lease_owner = row["lease_owner"] if row else None
            lease_expires_at = _parse_time(row["lease_expires_at"]) if row else None
            last_attempt_at = _parse_time(row["last_attempt_at"]) if row else None

            if lease_owner and lease_expires_at and lease_expires_at > current:
                return (
                    DiscoveryWakeDisposition.LEASE_HELD,
                    f"lease held until {lease_expires_at.isoformat()}",
                )

            if last_attempt_at is not None:
                next_due = last_attempt_at + timedelta(seconds=policy.minimum_interval_seconds)
                if current < next_due:
                    return DiscoveryWakeDisposition.NOT_DUE, f"next wake due at {next_due.isoformat()}"

            lease_expires = current + timedelta(seconds=policy.lease_seconds)
            if row is None:
                db.execute(
                    "INSERT INTO discovery_wake_state "
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
                    "UPDATE discovery_wake_state SET lease_owner = ?, lease_expires_at = ?, "
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
        result_id: UUID | None,
        mark_completed: bool,
    ) -> None:
        current = _utc(now)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT lease_owner FROM discovery_wake_state WHERE name = ?",
                (self.lease_name,),
            ).fetchone()
            if row is None or row["lease_owner"] != str(owner_id):
                raise DiscoveryWakeLeaseLost("discovery wake lease is no longer owned by this worker")

            if mark_completed:
                db.execute(
                    "UPDATE discovery_wake_state SET lease_owner = NULL, lease_expires_at = NULL, "
                    "last_completed_at = ?, last_result_id = ?, updated_at = ? WHERE name = ?",
                    (
                        current.isoformat(),
                        str(result_id) if result_id else None,
                        current.isoformat(),
                        self.lease_name,
                    ),
                )
            else:
                db.execute(
                    "UPDATE discovery_wake_state SET lease_owner = NULL, lease_expires_at = NULL, "
                    "updated_at = ? WHERE name = ?",
                    (current.isoformat(), self.lease_name),
                )


class DiscoveryWakeController:
    """Cron-safe due gate and expiring single-worker lease around one discovery runtime tick."""

    def __init__(self, engine: HelisEngine, runtime: DiscoveryRuntime) -> None:
        self.engine = engine
        self.runtime = runtime
        self.state = DiscoveryWakeStore(engine)

    def wake(
        self,
        policy: DiscoveryWakePolicy | None = None,
        *,
        now: datetime | None = None,
    ) -> DiscoveryWakeResult:
        wake_policy = policy or DiscoveryWakePolicy()
        attempted_at = _utc(now or utc_now())
        owner_id = uuid4()
        disposition, reason = self.state.acquire(
            wake_policy,
            owner_id=owner_id,
            now=attempted_at,
        )
        if disposition is not None:
            result = DiscoveryWakeResult(
                disposition=disposition,
                reason=reason,
                attempted_at=attempted_at,
            )
            self._record(result)
            return result

        try:
            runtime_result = self.runtime.tick(wake_policy)
            completed_at = _utc(now or runtime_result.completed_at or utc_now())
            result = runtime_result.model_copy(
                update={
                    "owner_id": owner_id,
                    "attempted_at": attempted_at,
                    "completed_at": completed_at,
                }
            )
            self.state.finish_owned(
                owner_id=owner_id,
                now=completed_at,
                result_id=result.id,
                mark_completed=True,
            )
        except Exception as exc:  # noqa: BLE001 -- wake boundary isolates source/model/runtime failures
            completed_at = _utc(now or utc_now())
            try:
                self.state.finish_owned(
                    owner_id=owner_id,
                    now=completed_at,
                    result_id=None,
                    mark_completed=False,
                )
                failure_reason = _safe_failure_reason(exc)
            except DiscoveryWakeLeaseLost as lease_exc:
                failure_reason = _safe_failure_reason(
                    RuntimeError(f"{_safe_failure_reason(exc)}; {lease_exc}")
                )
            result = DiscoveryWakeResult(
                disposition=DiscoveryWakeDisposition.FAILED,
                owner_id=owner_id,
                reason=failure_reason,
                attempted_at=attempted_at,
                completed_at=completed_at,
            )

        self._record(result)
        return result

    def _record(self, result: DiscoveryWakeResult) -> None:
        self.state.save_result(result)
        self.engine.store.append_event(
            AuditEvent(
                event_type="discovery.wake",
                entity_id=result.id,
                data={
                    "disposition": result.disposition.value,
                    "reason": result.reason,
                    "observations_fetched": result.observations_fetched,
                    "observations_new": result.observations_new,
                    "source_failures": result.source_failures,
                    "observations_used": result.observations_used,
                    "candidates_discovered": result.candidates_discovered,
                    "candidates_evaluated": result.candidates_evaluated,
                    "experiments_planned": result.experiments_planned,
                    "budget_exhausted": result.budget_exhausted,
                    "model_calls": result.model_calls,
                    "tokens": result.tokens,
                    "cost_cents": result.cost_cents,
                },
            )
        )
