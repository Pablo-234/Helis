from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.analyst import OpportunityAnalyst
from helis.budget import BudgetExceeded, CycleBudget
from helis.domain import AuditEvent, VentureStage, utc_now
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.scout import OpportunityScout
from helis.source_registry import SourceRegistry


class MarketScanDisposition(StrEnum):
    SCANNED = "scanned"
    NOT_DUE = "not_due"
    LEASE_HELD = "lease_held"
    FAILED = "failed"


class MarketDiscoveryPolicy(BaseModel):
    scan_interval_seconds: int = Field(default=21_600, ge=60, le=604_800)
    scan_lease_seconds: int = Field(default=600, ge=30, le=86_400)
    observation_limit: int = Field(default=100, ge=1, le=1000)
    candidate_limit: int = Field(default=2, ge=1, le=10)
    max_model_calls: int = Field(default=3, ge=0, le=20)
    max_tokens: int = Field(default=40_000, ge=0, le=500_000)
    max_cost_cents: float = Field(default=10.0, ge=0, le=10_000)


class MarketDiscoveryReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    scan_disposition: MarketScanDisposition
    scan_reason: str = Field(min_length=2, max_length=1000)
    scan_fetched: int = Field(default=0, ge=0)
    new_observations: int = Field(default=0, ge=0)
    source_failures: list[str] = Field(default_factory=list, max_length=50)
    observations_processed: int = Field(default=0, ge=0)
    candidates_discovered: int = Field(default=0, ge=0)
    candidates_evaluated: int = Field(default=0, ge=0)
    budget_exhausted: bool = False
    model_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    cost_cents: float = Field(default=0, ge=0)
    reason: str = Field(min_length=2, max_length=1200)
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def did_work(self) -> bool:
        return bool(
            self.new_observations
            or self.observations_processed
            or self.candidates_discovered
            or self.candidates_evaluated
        )


Clock = Callable[[], datetime]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_time(value: str | None) -> datetime | None:
    return _utc(datetime.fromisoformat(value)) if value else None


class MarketDiscoveryStore:
    lease_name = "market-discovery-scan"

    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_discovery_scan_state (
                    name TEXT PRIMARY KEY,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_attempt_at TEXT,
                    last_completed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS market_discovery_reports (
                    id TEXT PRIMARY KEY,
                    scan_disposition TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_market_discovery_reports_created
                    ON market_discovery_reports(created_at);
                """
            )

    def acquire_scan(
        self,
        policy: MarketDiscoveryPolicy,
        *,
        owner_id: UUID,
        now: datetime,
    ) -> tuple[MarketScanDisposition | None, str]:
        current = _utc(now)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM market_discovery_scan_state WHERE name = ?",
                (self.lease_name,),
            ).fetchone()
            lease_owner = row["lease_owner"] if row else None
            lease_expires_at = _parse_time(row["lease_expires_at"]) if row else None
            last_completed_at = _parse_time(row["last_completed_at"]) if row else None

            if lease_owner and lease_expires_at and lease_expires_at > current:
                return (
                    MarketScanDisposition.LEASE_HELD,
                    f"market scan lease held until {lease_expires_at.isoformat()}",
                )

            if last_completed_at is not None:
                next_due = last_completed_at + timedelta(seconds=policy.scan_interval_seconds)
                if current < next_due:
                    return MarketScanDisposition.NOT_DUE, f"next market scan due at {next_due.isoformat()}"

            lease_expires = current + timedelta(seconds=policy.scan_lease_seconds)
            if row is None:
                db.execute(
                    "INSERT INTO market_discovery_scan_state "
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
                    "UPDATE market_discovery_scan_state SET lease_owner = ?, lease_expires_at = ?, "
                    "last_attempt_at = ?, updated_at = ? WHERE name = ?",
                    (
                        str(owner_id),
                        lease_expires.isoformat(),
                        current.isoformat(),
                        current.isoformat(),
                        self.lease_name,
                    ),
                )
        return None, "market_scan_lease_acquired"

    def finish_scan(self, *, owner_id: UUID, now: datetime, completed: bool) -> None:
        current = _utc(now)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT lease_owner FROM market_discovery_scan_state WHERE name = ?",
                (self.lease_name,),
            ).fetchone()
            if row is None or row["lease_owner"] != str(owner_id):
                return
            if completed:
                db.execute(
                    "UPDATE market_discovery_scan_state SET lease_owner = NULL, "
                    "lease_expires_at = NULL, last_completed_at = ?, updated_at = ? WHERE name = ?",
                    (current.isoformat(), current.isoformat(), self.lease_name),
                )
            else:
                db.execute(
                    "UPDATE market_discovery_scan_state SET lease_owner = NULL, "
                    "lease_expires_at = NULL, updated_at = ? WHERE name = ?",
                    (current.isoformat(), self.lease_name),
                )

    def save_report(self, report: MarketDiscoveryReport) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO market_discovery_reports "
                "(id, scan_disposition, payload, created_at) VALUES (?, ?, ?, ?)",
                (
                    str(report.id),
                    report.scan_disposition.value,
                    report.model_dump_json(),
                    report.created_at.isoformat(),
                ),
            )

    def latest(self) -> MarketDiscoveryReport | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM market_discovery_reports ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return MarketDiscoveryReport.model_validate_json(row["payload"]) if row else None


class MarketDiscoveryMachine:
    """Periodic source scan plus bounded scout/score work; never validates, builds or contacts."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        *,
        config_path: str | Path = "helis.toml",
        policy: MarketDiscoveryPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.config_path = Path(config_path)
        self.policy = policy or MarketDiscoveryPolicy()
        self.clock = clock or utc_now
        self.state = MarketDiscoveryStore(engine)

    def tick(self, *, now: datetime | None = None) -> MarketDiscoveryReport:
        current = _utc(now or self.clock())
        scan_disposition, scan_reason, scan_fetched, new_observations, failures = self._scan_if_due(
            current
        )
        budget = CycleBudget(
            max_model_calls=self.policy.max_model_calls,
            max_tokens=self.policy.max_tokens,
            max_cost_cents=self.policy.max_cost_cents,
        )
        observations_processed = 0
        discovered = 0
        evaluated = 0
        exhausted = False
        runtime_errors: list[str] = []

        unprocessed = self.engine.store.list_unprocessed_observations(
            limit=self.policy.observation_limit
        )
        if unprocessed and self.policy.max_model_calls > 0:
            try:
                generated = OpportunityScout(self.provider, budget).discover(unprocessed)
                discovered = len(generated)
                for candidate in generated:
                    self.engine.ingest(candidate)
                self.engine.store.mark_observations_processed(item.id for item in unprocessed)
                observations_processed = len(unprocessed)
            except BudgetExceeded:
                exhausted = True
            except Exception as exc:  # noqa: BLE001 -- market lane must not kill portfolio execution
                runtime_errors.append(f"scout {type(exc).__name__}: {exc}")

        pending = sorted(
            (
                item
                for item in self.engine.store.list_opportunities()
                if item.stage == VentureStage.DISCOVERED
            ),
            key=lambda item: (item.discovered_at, str(item.id)),
        )
        analyst = OpportunityAnalyst(self.provider, budget)
        for candidate in pending[: self.policy.candidate_limit]:
            try:
                assessment = analyst.assess(candidate)
            except BudgetExceeded:
                exhausted = True
                break
            except Exception as exc:  # noqa: BLE001 -- isolate one failed market assessment
                runtime_errors.append(
                    f"analysis {candidate.id} {type(exc).__name__}: {exc}"
                )
                continue
            self.engine.evaluate(candidate, assessment.dimensions)
            evaluated += 1

        reason_parts: list[str] = []
        if runtime_errors:
            reason_parts.extend(runtime_errors)
        if exhausted:
            reason_parts.append("market discovery model budget exhausted")
        if discovered or evaluated:
            reason_parts.append(f"discovered={discovered} evaluated={evaluated}")
        elif new_observations:
            reason_parts.append(f"stored {new_observations} new observation(s)")
        elif not reason_parts:
            reason_parts.append("no new market work")

        report = MarketDiscoveryReport(
            scan_disposition=scan_disposition,
            scan_reason=scan_reason,
            scan_fetched=scan_fetched,
            new_observations=new_observations,
            source_failures=failures,
            observations_processed=observations_processed,
            candidates_discovered=discovered,
            candidates_evaluated=evaluated,
            budget_exhausted=exhausted,
            model_calls=budget.model_calls,
            tokens=budget.tokens,
            cost_cents=budget.cost_cents,
            reason="; ".join(reason_parts)[:1200],
            created_at=current,
        )
        self._record(report)
        return report

    def _scan_if_due(
        self,
        current: datetime,
    ) -> tuple[MarketScanDisposition, str, int, int, list[str]]:
        owner_id = uuid4()
        disposition, reason = self.state.acquire_scan(
            self.policy,
            owner_id=owner_id,
            now=current,
        )
        if disposition is not None:
            return disposition, reason, 0, 0, []

        if not self.config_path.is_file():
            self.state.finish_scan(owner_id=owner_id, now=current, completed=True)
            return (
                MarketScanDisposition.FAILED,
                f"market source config not found: {self.config_path}",
                0,
                0,
                [],
            )

        try:
            scan = SourceRegistry.from_toml(self.config_path).scan()
        except Exception as exc:  # noqa: BLE001 -- invalid/unreadable config is isolated from portfolio
            self.state.finish_scan(owner_id=owner_id, now=current, completed=False)
            return (
                MarketScanDisposition.FAILED,
                f"market scan {type(exc).__name__}: {exc}",
                0,
                0,
                [],
            )

        inserted = 0
        for observation in scan.observations:
            if not self.engine.store.save_observation(observation):
                continue
            inserted += 1
            self.engine.store.append_event(
                AuditEvent(
                    event_type="market.observed",
                    entity_id=observation.id,
                    data={"source": observation.source},
                )
            )
        self.state.finish_scan(owner_id=owner_id, now=current, completed=True)
        failures = [f"{item.source_name}: {item.error}" for item in scan.failures]
        return (
            MarketScanDisposition.SCANNED,
            "market source scan completed",
            len(scan.observations),
            inserted,
            failures,
        )

    def _record(self, report: MarketDiscoveryReport) -> None:
        self.state.save_report(report)
        self.engine.store.append_event(
            AuditEvent(
                event_type="market.discovery_tick",
                entity_id=report.id,
                data={
                    "scan_disposition": report.scan_disposition.value,
                    "scan_fetched": report.scan_fetched,
                    "new_observations": report.new_observations,
                    "source_failure_count": len(report.source_failures),
                    "observations_processed": report.observations_processed,
                    "candidates_discovered": report.candidates_discovered,
                    "candidates_evaluated": report.candidates_evaluated,
                    "model_calls": report.model_calls,
                    "tokens": report.tokens,
                    "cost_cents": report.cost_cents,
                    "budget_exhausted": report.budget_exhausted,
                    "reason": report.reason,
                },
            )
        )
