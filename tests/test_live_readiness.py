from __future__ import annotations

from pathlib import Path

import pytest

import helis.live_readiness as live_module
import helis.local_model_runtime as local_model_module
from helis.autopilot import (
    AutopilotDiscoveryReport,
    AutopilotPolicy,
    AutopilotReport,
    AutopilotStopReason,
)
from helis.engine import HelisEngine
from helis.live_readiness import (
    DEFAULT_PILOT_CONFIG,
    LiveBootstrapper,
    LivePilotFailure,
    LivePilotRunner,
    LivePilotStatus,
    LivePilotStore,
    LiveReadinessInspector,
    ReadinessLevel,
    probe_local_model_endpoint,
)
from helis.model_provider import OpenAICompatibleProvider
from helis.source_registry import RegistryScanResult
from helis.store import HelisStore


class EmptyScanner:
    def scan(self) -> RegistryScanResult:
        return RegistryScanResult()


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "config": tmp_path / "config" / "helis.toml",
        "db": tmp_path / "state" / "helis.db",
        "workspace_root": tmp_path / "runtime" / "workspaces",
        "self_improvement_root": tmp_path / "runtime" / "self-improvement",
    }


def _provider(**updates) -> OpenAICompatibleProvider:
    values = {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3.5:9b",
    }
    values.update(updates)
    return OpenAICompatibleProvider(**values)


def test_bootstrap_creates_safe_state_and_never_overwrites_config(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    bootstrapper = LiveBootstrapper(**paths)

    first = bootstrapper.run()

    assert first.config_created is True
    assert first.database_created is True
    assert paths["config"].read_text(encoding="utf-8") == DEFAULT_PILOT_CONFIG
    assert paths["db"].is_file()
    assert paths["workspace_root"].is_dir()
    assert paths["self_improvement_root"].is_dir()

    custom = '# operator-owned config\n[[sources]]\nname="ask"\nkind="hacker_news"\n'
    paths["config"].write_text(custom, encoding="utf-8")
    second = bootstrapper.run()

    assert second.config_created is False
    assert second.database_created is False
    assert paths["config"].read_text(encoding="utf-8") == custom
    events = HelisStore(paths["db"]).list_events()
    assert [item.event_type for item in events].count("live.bootstrap") == 2


def test_doctor_reports_required_blocks_and_optional_warnings(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    report = LiveReadinessInspector(
        _provider(),
        **paths,
        systemd_user_root=tmp_path / "systemd",
    ).inspect()

    assert report.pilot_ready is False
    assert {item.key for item in report.blocking} == {"config"}
    assert any(item.key == "timers" and item.level == ReadinessLevel.WARNING for item in report.checks)

    LiveBootstrapper(**paths).run()
    ready = LiveReadinessInspector(
        _provider(),
        **paths,
        systemd_user_root=tmp_path / "systemd",
    ).inspect()

    assert ready.pilot_ready is True
    assert not ready.blocking


def test_doctor_blocks_remote_model_for_zero_spend_pilot(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    LiveBootstrapper(**paths).run()

    report = LiveReadinessInspector(
        _provider(base_url="https://paid-model.example/v1"),
        **paths,
    ).inspect()

    model = next(item for item in report.checks if item.key == "model")
    assert model.level == ReadinessLevel.BLOCKED
    assert model.required_for_pilot is True
    assert report.pilot_ready is False


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        (_provider(api_key="secret"), "credentials"),
        (_provider(output_cost_per_million_tokens=1), "token prices"),
    ],
)
def test_doctor_blocks_model_credentials_and_configured_prices(
    tmp_path: Path,
    provider: OpenAICompatibleProvider,
    message: str,
) -> None:
    paths = _paths(tmp_path)
    LiveBootstrapper(**paths).run()

    report = LiveReadinessInspector(provider, **paths).inspect()

    model = next(item for item in report.checks if item.key == "model")
    assert model.level == ReadinessLevel.BLOCKED
    assert message in model.detail
    assert report.pilot_ready is False


def test_local_probe_reads_models_metadata_without_credentials(monkeypatch) -> None:
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"data":[{"id":"qwen3.5:9b"}]}'

    def fake_urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(local_model_module, "urlopen", fake_urlopen)

    ready, detail = probe_local_model_endpoint(_provider(), timeout_seconds=1.5)

    assert ready is True
    assert "configured model is present" in detail
    assert captured == {
        "url": "http://localhost:11434/v1/models",
        "headers": {"Accept": "application/json"},
        "timeout": 1.5,
    }


def test_pilot_uses_no_external_gateways_and_persists_audited_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    captured = {}

    class FakeOperator:
        def __init__(self, *args, **kwargs) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

        def run(self, policy: AutopilotPolicy) -> AutopilotReport:
            captured["policy"] = policy
            return AutopilotReport(
                discovery=AutopilotDiscoveryReport(),
                stop_reason=AutopilotStopReason.NO_PROGRESS,
                blockers=["no bounded work available"],
            )

    monkeypatch.setattr(live_module, "AutonomousOnlineVentureOperator", FakeOperator)
    workspace = tmp_path / "workspaces"
    runner = LivePilotRunner(
        engine,
        _provider(),
        EmptyScanner,
        workspace_root=workspace,
    )

    report = runner.run(
        AutopilotPolicy(
            cash_cents=0,
            discovery_max_cost_cents=0,
            portfolio_model_calls=10,
            max_rounds=1,
        )
    )

    assert captured["kwargs"] == {"workspace_root": workspace}
    assert captured["policy"].cash_cents == 0
    assert report.cash_limit_cents == 0
    assert report.status == LivePilotStatus.COMPLETED
    assert report.external_write_gateways_enabled is False
    assert report.operator_items == []
    stored = LivePilotStore(engine).latest()
    assert stored is not None and stored.id == report.id
    assert any(item.event_type == "live.pilot_completed" for item in engine.store.list_events())


def test_failed_pilot_is_persisted_and_audited(tmp_path: Path, monkeypatch) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))

    class FailingOperator:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self, policy: AutopilotPolicy) -> AutopilotReport:
            raise RuntimeError("local model returned malformed output")

    monkeypatch.setattr(live_module, "AutonomousOnlineVentureOperator", FailingOperator)
    runner = LivePilotRunner(
        engine,
        _provider(),
        EmptyScanner,
        workspace_root=tmp_path / "workspaces",
    )

    with pytest.raises(LivePilotFailure, match="malformed output") as captured:
        runner.run(AutopilotPolicy(discovery_max_cost_cents=0))

    assert captured.value.report.status == LivePilotStatus.FAILED
    stored = LivePilotStore(engine).latest()
    assert stored is not None and stored.status == LivePilotStatus.FAILED
    assert stored.error == "RuntimeError: local model returned malformed output"
    assert any(item.event_type == "live.pilot_failed" for item in engine.store.list_events())


@pytest.mark.parametrize(
    ("provider", "policy", "message"),
    [
        (
            _provider(base_url="https://remote.example/v1"),
            AutopilotPolicy(discovery_max_cost_cents=0),
            "localhost",
        ),
        (
            _provider(api_key="secret"),
            AutopilotPolicy(discovery_max_cost_cents=0),
            "credentials",
        ),
        (
            _provider(input_cost_per_million_tokens=1),
            AutopilotPolicy(discovery_max_cost_cents=0),
            "token prices",
        ),
        (
            _provider(),
            AutopilotPolicy(cash_cents=1, discovery_max_cost_cents=0),
            "cash limit",
        ),
        (
            _provider(),
            AutopilotPolicy(discovery_max_cost_cents=1),
            "model cost limit",
        ),
    ],
)
def test_pilot_fails_closed_on_expanded_authority(
    tmp_path: Path,
    provider: OpenAICompatibleProvider,
    policy: AutopilotPolicy,
    message: str,
) -> None:
    runner = LivePilotRunner(
        HelisEngine(HelisStore(tmp_path / "helis.db")),
        provider,
        EmptyScanner,
        workspace_root=tmp_path / "workspaces",
    )

    with pytest.raises(ValueError, match=message):
        runner.run(policy)
