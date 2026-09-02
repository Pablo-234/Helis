from __future__ import annotations

import subprocess
from pathlib import Path

from helis.host_scheduler import HostSchedulerInspector
from helis.live_activation import LiveActivationInspector
from helis.live_readiness import LiveBootstrapper, ReadinessLevel
from helis.model_provider import OpenAICompatibleProvider


LIVE_ENVIRONMENT = [
    "HELIS_VERCEL_TOKEN",
    "HELIS_VERCEL_ORG_ID",
    "HELIS_VERCEL_PROJECT_ID",
    "HELIS_BRAVE_SEARCH_API_KEY",
    "HELIS_BRAVE_COUNTRY",
    "HELIS_BRAVE_SEARCH_LANG",
    "HELIS_RESEND_API_KEY",
    "HELIS_RESEND_FROM",
    "HELIS_RESEND_INBOUND_DOMAIN",
    "HELIS_STRIPE_SECRET_KEY",
    "HELIS_PREVIEW_GATEWAY_URL",
    "HELIS_PROSPECT_GATEWAY_URL",
    "HELIS_CONTACT_GATEWAY_URL",
    "HELIS_CONTACT_RESULT_GATEWAY_URL",
    "HELIS_COMMERCE_GATEWAY_URL",
    "HELIS_VALIDATION_GATEWAY_URL",
    "HELIS_VALIDATION_GATEWAY_TOKEN",
]


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "config": tmp_path / "helis.toml",
        "db": tmp_path / "helis.db",
        "workspace_root": tmp_path / "workspaces",
        "self_improvement_root": tmp_path / "self-improvement",
    }


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        model="qwen3.5:9b",
    )


def _configure_live(monkeypatch) -> None:
    for name in LIVE_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HELIS_VERCEL_TOKEN", "vercel-secret")
    monkeypatch.setenv("HELIS_VERCEL_ORG_ID", "team_helis")
    monkeypatch.setenv("HELIS_VERCEL_PROJECT_ID", "prj_helis")
    monkeypatch.setenv("HELIS_BRAVE_SEARCH_API_KEY", "brave-secret")
    monkeypatch.setenv("HELIS_BRAVE_COUNTRY", "PL")
    monkeypatch.setenv("HELIS_BRAVE_SEARCH_LANG", "pl")
    monkeypatch.setenv("HELIS_RESEND_API_KEY", "resend-secret")
    monkeypatch.setenv("HELIS_RESEND_FROM", "HELIS <hello@example.test>")
    monkeypatch.setenv("HELIS_RESEND_INBOUND_DOMAIN", "inbound.resend.app")
    monkeypatch.setenv("HELIS_STRIPE_SECRET_KEY", "stripe-secret")
    monkeypatch.setenv("HELIS_VALIDATION_GATEWAY_URL", "https://validation.example.test/helis")
    monkeypatch.setenv("HELIS_VALIDATION_GATEWAY_TOKEN", "validation-secret")


def _host_scheduler(*, installed: int) -> HostSchedulerInspector:
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0 if calls <= installed else 1)

    return HostSchedulerInspector(platform_name="Windows", runner=fake_run)


def test_activation_is_ready_before_registration_when_complete_pipeline_is_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    LiveBootstrapper(**paths).run()
    _configure_live(monkeypatch)

    report = LiveActivationInspector(
        _provider(),
        **paths,
        host_scheduler=_host_scheduler(installed=0),
        which=lambda executable: f"C:/tools/{executable}.exe",
    ).inspect(require_schedule=False)

    assert report.activation_ready is True
    assert not report.blocking
    gateway_checks = [item for item in report.checks if item.key.startswith("gateway_")]
    assert len(gateway_checks) == 6
    assert all(item.level == ReadinessLevel.READY for item in gateway_checks)
    timers = next(item for item in report.checks if item.key == "timers")
    assert timers.level == ReadinessLevel.WARNING
    assert timers.required_for_activation is False


def test_activation_requires_complete_installed_schedule_when_requested(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    LiveBootstrapper(**paths).run()
    _configure_live(monkeypatch)

    report = LiveActivationInspector(
        _provider(),
        **paths,
        host_scheduler=_host_scheduler(installed=1),
        which=lambda executable: f"C:/tools/{executable}.exe",
    ).inspect(require_schedule=True)

    assert report.activation_ready is False
    timers = next(item for item in report.blocking if item.key == "timers")
    assert timers.level == ReadinessLevel.BLOCKED
    assert timers.required_for_activation is True
    assert "1/2 Windows Task Scheduler" in timers.detail


def test_activation_blocks_a_missing_reply_observation_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    LiveBootstrapper(**paths).run()
    _configure_live(monkeypatch)
    monkeypatch.delenv("HELIS_RESEND_INBOUND_DOMAIN")

    report = LiveActivationInspector(
        _provider(),
        **paths,
        host_scheduler=_host_scheduler(installed=2),
        which=lambda executable: f"C:/tools/{executable}.exe",
    ).inspect(require_schedule=True)

    assert report.activation_ready is False
    assert {item.key for item in report.blocking} == {"gateway_contact_result"}


def test_activation_blocks_without_external_validation_transport(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    LiveBootstrapper(**paths).run()
    _configure_live(monkeypatch)
    monkeypatch.delenv("HELIS_VALIDATION_GATEWAY_URL")

    report = LiveActivationInspector(
        _provider(),
        **paths,
        host_scheduler=_host_scheduler(installed=2),
        which=lambda executable: f"C:/tools/{executable}.exe",
    ).inspect(require_schedule=True)

    assert report.activation_ready is False
    assert {item.key for item in report.blocking} == {"gateway_validation"}


def test_activation_blocks_direct_preview_when_vercel_cli_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    LiveBootstrapper(**paths).run()
    _configure_live(monkeypatch)

    report = LiveActivationInspector(
        _provider(),
        **paths,
        host_scheduler=_host_scheduler(installed=2),
        which=lambda executable: None,
    ).inspect(require_schedule=True)

    assert report.activation_ready is False
    assert {item.key for item in report.blocking} == {"gateway_preview"}
    assert "not available on PATH" in report.blocking[0].detail


def test_activation_fails_closed_on_invalid_adapter_configuration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    LiveBootstrapper(**paths).run()
    _configure_live(monkeypatch)
    monkeypatch.setenv("HELIS_BRAVE_COUNTRY", "POL")

    report = LiveActivationInspector(
        _provider(),
        **paths,
        host_scheduler=_host_scheduler(installed=2),
        which=lambda executable: f"C:/tools/{executable}.exe",
    ).inspect(require_schedule=True)

    assert report.activation_ready is False
    assert {item.key for item in report.blocking} == {"gateway_configuration"}
    assert "two-letter code" in report.blocking[0].detail
