from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from helis.autopilot import (
    AutonomousOnlineVentureOperator,
    AutopilotPolicy,
    AutopilotReport,
)
from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.host_scheduler import HostSchedulerInspector
from helis.live_gateway_factory import live_gateways_from_env
from helis.local_model_runtime import (
    LocalModelInspector,
    LocalModelState,
    is_local_model_endpoint,
)
from helis.model_provider import OpenAICompatibleProvider
from helis.operator_domain import OperatorInboxItem
from helis.operator_inbox import OperatorInbox
from helis.source_registry import SourceRegistry
from helis.store import HelisStore
from helis.validation_gateway import ApprovedValidationGateway

DEFAULT_PILOT_CONFIG = """# Safe default market sources for a HELIS pilot.
# Network reads are allowed; publication, contact and spend stay separately gated.

[[sources]]
name = "HN Ask"
kind = "hacker_news"
feed = "ask"
limit = 40
comments_per_story = 2
comment_limit = 40

[[sources]]
name = "HN Show"
kind = "hacker_news"
feed = "show"
limit = 30
comments_per_story = 2
comment_limit = 30
"""


class ReadinessLevel(StrEnum):
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"


class ReadinessCheck(BaseModel):
    key: str = Field(min_length=2, max_length=80)
    label: str = Field(min_length=2, max_length=160)
    level: ReadinessLevel
    detail: str = Field(min_length=1, max_length=2000)
    required_for_pilot: bool = False


class LiveReadinessReport(BaseModel):
    checks: list[ReadinessCheck]
    pilot_ready: bool
    inspected_at: datetime = Field(default_factory=utc_now)

    @property
    def blocking(self) -> list[ReadinessCheck]:
        return [
            item
            for item in self.checks
            if item.required_for_pilot and item.level == ReadinessLevel.BLOCKED
        ]


class LiveBootstrapReport(BaseModel):
    config: Path
    database: Path
    workspace_root: Path
    self_improvement_root: Path
    config_created: bool
    database_created: bool
    directories_created: list[Path] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=utc_now)


class LivePilotStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class LivePilotReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    started_at: datetime
    completed_at: datetime = Field(default_factory=utc_now)
    status: LivePilotStatus
    autopilot: AutopilotReport | None = None
    operator_items: list[OperatorInboxItem] = Field(default_factory=list)
    cash_limit_cents: int = 0
    external_write_gateways_enabled: bool = False
    error: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_outcome(self) -> LivePilotReport:
        if self.status == LivePilotStatus.COMPLETED and self.autopilot is None:
            raise ValueError("completed pilot requires an autopilot report")
        if self.status == LivePilotStatus.FAILED and not self.error:
            raise ValueError("failed pilot requires an error")
        return self


class LivePilotFailure(RuntimeError):
    def __init__(self, report: LivePilotReport) -> None:
        self.report = report
        super().__init__(report.error or "controlled pilot failed")


class PilotScanner(Protocol):
    def scan(self): ...


ScannerFactory = Callable[[], PilotScanner]


def probe_local_model_endpoint(
    provider: OpenAICompatibleProvider,
    *,
    timeout_seconds: float = 3.0,
) -> tuple[bool, str]:
    """Verify that the exact configured model is present; never request a completion."""
    report = LocalModelInspector(provider).inspect(timeout_seconds=timeout_seconds)
    return report.state == LocalModelState.READY, f"{report.detail}; next: {report.next_command}"


class LiveBootstrapper:
    def __init__(
        self,
        *,
        config: str | Path,
        db: str | Path,
        workspace_root: str | Path,
        self_improvement_root: str | Path,
    ) -> None:
        self.config = Path(config).expanduser()
        self.db = Path(db).expanduser()
        self.workspace_root = Path(workspace_root).expanduser()
        self.self_improvement_root = Path(self_improvement_root).expanduser()

    def run(self) -> LiveBootstrapReport:
        created: list[Path] = []
        for directory in (
            self.config.parent,
            self.db.parent,
            self.workspace_root,
            self.self_improvement_root,
        ):
            if directory.exists() and not directory.is_dir():
                raise ValueError(f"required directory path is a file: {directory}")
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                created.append(directory.resolve())

        config_created = False
        if self.config.exists():
            if not self.config.is_file():
                raise ValueError(f"configuration path is not a file: {self.config}")
        else:
            with self.config.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(DEFAULT_PILOT_CONFIG)
            config_created = True

        database_created = not self.db.exists()
        engine = HelisEngine(HelisStore(self.db))
        report = LiveBootstrapReport(
            config=self.config.resolve(),
            database=self.db.resolve(),
            workspace_root=self.workspace_root.resolve(),
            self_improvement_root=self.self_improvement_root.resolve(),
            config_created=config_created,
            database_created=database_created,
            directories_created=created,
        )
        engine.store.append_event(
            AuditEvent(
                event_type="live.bootstrap",
                data={
                    "config": str(report.config),
                    "database": str(report.database),
                    "workspace_root": str(report.workspace_root),
                    "self_improvement_root": str(report.self_improvement_root),
                    "config_created": report.config_created,
                    "database_created": report.database_created,
                },
            )
        )
        return report


class LiveReadinessInspector:
    def __init__(
        self,
        provider: OpenAICompatibleProvider,
        *,
        config: str | Path,
        db: str | Path,
        workspace_root: str | Path,
        self_improvement_root: str | Path,
        systemd_user_root: str | Path | None = None,
        host_scheduler: HostSchedulerInspector | None = None,
    ) -> None:
        self.provider = provider
        self.config = Path(config).expanduser()
        self.db = Path(db).expanduser()
        self.workspace_root = Path(workspace_root).expanduser()
        self.self_improvement_root = Path(self_improvement_root).expanduser()
        self.host_scheduler = host_scheduler or HostSchedulerInspector(
            systemd_user_root=systemd_user_root
        )

    def inspect(self, *, probe_model: bool = False) -> LiveReadinessReport:
        checks = [
            self._config_check(),
            self._path_check("database", "Database location", self.db, target_is_file=True),
            self._path_check("workspace", "Venture workspace", self.workspace_root),
            self._path_check(
                "self_improvement",
                "Self-improvement workspace",
                self.self_improvement_root,
            ),
            self._model_check(probe_model=probe_model),
            self._sandbox_check(),
            self._timer_check(),
            self._gateway_check(),
        ]
        ready = not any(
            item.required_for_pilot and item.level == ReadinessLevel.BLOCKED
            for item in checks
        )
        return LiveReadinessReport(checks=checks, pilot_ready=ready)

    def _config_check(self) -> ReadinessCheck:
        if not self.config.is_file():
            return self._check(
                "config",
                "Market sources",
                ReadinessLevel.BLOCKED,
                f"missing configuration: {self.config.resolve()}",
                required=True,
            )
        try:
            registry = SourceRegistry.from_toml(self.config)
            enabled = sum(item.enabled for item in registry.config.sources)
        except Exception as exc:  # noqa: BLE001 -- doctor must report malformed config
            return self._check(
                "config",
                "Market sources",
                ReadinessLevel.BLOCKED,
                f"{type(exc).__name__}: {exc}",
                required=True,
            )
        if enabled == 0:
            return self._check(
                "config",
                "Market sources",
                ReadinessLevel.BLOCKED,
                "configuration parses but has no enabled sources",
                required=True,
            )
        return self._check(
            "config",
            "Market sources",
            ReadinessLevel.READY,
            f"{enabled}/{len(registry.config.sources)} sources enabled in {self.config.resolve()}",
            required=True,
        )

    def _model_check(self, *, probe_model: bool) -> ReadinessCheck:
        if not self.provider.model or not self.provider.base_url:
            return self._check(
                "model",
                "Local model",
                ReadinessLevel.BLOCKED,
                "model name or base URL is missing",
                required=True,
            )
        if not is_local_model_endpoint(self.provider.base_url):
            return self._check(
                "model",
                "Local model",
                ReadinessLevel.BLOCKED,
                "zero-spend pilot requires a localhost OpenAI-compatible endpoint",
                required=True,
            )
        if self.provider.api_key:
            return self._check(
                "model",
                "Local model",
                ReadinessLevel.BLOCKED,
                "zero-spend pilot refuses model API credentials",
                required=True,
            )
        if any(
            (
                self.provider.input_cost_per_million_tokens,
                self.provider.output_cost_per_million_tokens,
            )
        ):
            return self._check(
                "model",
                "Local model",
                ReadinessLevel.BLOCKED,
                "zero-spend pilot requires configured input/output token prices of zero",
                required=True,
            )
        if not probe_model:
            return self._check(
                "model",
                "Local model",
                ReadinessLevel.READY,
                f"{self.provider.model} @ {self.provider.base_url}; connectivity not probed",
                required=True,
            )
        ready, detail = probe_local_model_endpoint(self.provider)
        return self._check(
            "model",
            "Local model",
            ReadinessLevel.READY if ready else ReadinessLevel.BLOCKED,
            detail,
            required=True,
        )

    def _sandbox_check(self) -> ReadinessCheck:
        selected = os.getenv("HELIS_EXECUTABLE_SANDBOX", "").strip().lower()
        if not selected:
            return self._check(
                "sandbox",
                "Executable sandbox",
                ReadinessLevel.WARNING,
                "disabled; static/manual MVP templates still work",
            )
        if selected != "docker":
            return self._check(
                "sandbox",
                "Executable sandbox",
                ReadinessLevel.WARNING,
                f"unsupported selection: {selected}",
            )
        executable = shutil.which("docker")
        image = os.getenv("HELIS_EXECUTABLE_SANDBOX_IMAGE", "python:3.12-alpine")
        return self._check(
            "sandbox",
            "Executable sandbox",
            ReadinessLevel.READY if executable else ReadinessLevel.WARNING,
            f"docker={executable or 'missing'}; configured image={image}",
        )

    def _timer_check(self) -> ReadinessCheck:
        report = self.host_scheduler.inspect()
        return self._check(
            "timers",
            "Continuous wake schedule",
            ReadinessLevel.READY if report.complete else ReadinessLevel.WARNING,
            report.detail,
        )

    def _gateway_check(self) -> ReadinessCheck:
        try:
            live = live_gateways_from_env()
            validation = ApprovedValidationGateway.from_env()
        except ValueError as exc:
            return self._check(
                "gateways",
                "External gateways",
                ReadinessLevel.WARNING,
                f"invalid optional gateway configuration: {exc}",
            )
        configured = [name for name in live.names.values() if name]
        if validation is not None:
            configured.append(validation.name)
        return self._check(
            "gateways",
            "External gateways",
            ReadinessLevel.WARNING,
            (
                f"{len(configured)} configured but deliberately disabled during pilot"
                if configured
                else "none configured; this is safe for pilot and blocks live external actions"
            ),
        )

    def _path_check(
        self,
        key: str,
        label: str,
        path: Path,
        *,
        target_is_file: bool = False,
    ) -> ReadinessCheck:
        if path.exists():
            valid_type = path.is_file() if target_is_file else path.is_dir()
            writable = valid_type and os.access(path, os.W_OK)
            detail = f"{path.resolve()} exists and is writable" if writable else (
                f"wrong path type or not writable: {path.resolve()}"
            )
        else:
            parent = path.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            writable = parent.is_dir() and os.access(parent, os.W_OK)
            detail = (
                f"{path.resolve()} will be created under writable {parent.resolve()}"
                if writable
                else f"no writable existing parent for {path.resolve()}"
            )
        return self._check(
            key,
            label,
            ReadinessLevel.READY if writable else ReadinessLevel.BLOCKED,
            detail,
            required=True,
        )

    @staticmethod
    def _check(
        key: str,
        label: str,
        level: ReadinessLevel,
        detail: str,
        *,
        required: bool = False,
    ) -> ReadinessCheck:
        return ReadinessCheck(
            key=key,
            label=label,
            level=level,
            detail=detail,
            required_for_pilot=required,
        )


class LivePilotStore:
    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS live_pilot_runs ("
                "id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)"
            )

    def save(self, report: LivePilotReport) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO live_pilot_runs (id, payload, created_at) VALUES (?, ?, ?)",
                (str(report.id), report.model_dump_json(), report.completed_at.isoformat()),
            )

    def latest(self) -> LivePilotReport | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM live_pilot_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return LivePilotReport.model_validate_json(row["payload"]) if row else None


class LivePilotRunner:
    """One real bounded HELIS run with local inference and no external-write gateways."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: OpenAICompatibleProvider,
        scanner_factory: ScannerFactory,
        *,
        workspace_root: str | Path,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.scanner_factory = scanner_factory
        self.workspace_root = Path(workspace_root)
        self.state = LivePilotStore(engine)

    def run(self, policy: AutopilotPolicy) -> LivePilotReport:
        if policy.cash_cents != 0:
            raise ValueError("live pilot cash limit must be zero")
        if policy.discovery_max_cost_cents != 0:
            raise ValueError("live pilot configured model cost limit must be zero")
        if not is_local_model_endpoint(self.provider.base_url):
            raise ValueError("live pilot requires a localhost model endpoint")
        if self.provider.api_key:
            raise ValueError("live pilot refuses model credentials")
        if any(
            (
                self.provider.input_cost_per_million_tokens,
                self.provider.output_cost_per_million_tokens,
            )
        ):
            raise ValueError("live pilot requires zero configured model token prices")
        started_at = utc_now()
        operator = AutonomousOnlineVentureOperator(
            self.engine,
            self.provider,
            self.scanner_factory,
            workspace_root=self.workspace_root,
        )
        try:
            autopilot = operator.run(policy)
        except Exception as exc:
            report = LivePilotReport(
                started_at=started_at,
                status=LivePilotStatus.FAILED,
                operator_items=OperatorInbox(self.engine).list_items(),
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
            self._record(report)
            raise LivePilotFailure(report) from exc
        report = LivePilotReport(
            started_at=started_at,
            status=LivePilotStatus.COMPLETED,
            autopilot=autopilot,
            operator_items=OperatorInbox(self.engine).list_items(),
        )
        self._record(report)
        return report

    def _record(self, report: LivePilotReport) -> None:
        self.state.save(report)
        autopilot = report.autopilot
        self.engine.store.append_event(
            AuditEvent(
                event_type=f"live.pilot_{report.status.value}",
                entity_id=report.id,
                data={
                    "stop_reason": autopilot.stop_reason.value if autopilot else None,
                    "funded_ventures": autopilot.funded_ventures if autopilot else 0,
                    "total_advanced": autopilot.total_advanced if autopilot else 0,
                    "operator_items": len(report.operator_items),
                    "cash_limit_cents": report.cash_limit_cents,
                    "external_write_gateways_enabled": report.external_write_gateways_enabled,
                    "error": report.error,
                },
            )
        )
