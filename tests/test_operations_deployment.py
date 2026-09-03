from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from helis import discovery_cli
from helis.discovery_cli import health as discovery_health
from helis.discovery_wake import DiscoveryWakeDisposition, DiscoveryWakeResult
from helis.scheduler_cli import health

ROOT = Path(__file__).resolve().parents[1]


def test_discovery_wake_cli_returns_nonzero_for_persisted_failure(monkeypatch) -> None:
    failed = DiscoveryWakeResult(
        disposition=DiscoveryWakeDisposition.FAILED,
        reason="ModelResponseError: empty final content",
        attempted_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
    )

    class Controller:
        def __init__(self, engine, runtime) -> None:
            pass

        def wake(self, policy):
            return failed

    monkeypatch.setattr(discovery_cli, "_runtime", lambda db, config: (object(), object()))
    monkeypatch.setattr(discovery_cli, "DiscoveryWakeController", Controller)

    result = CliRunner().invoke(discovery_cli.app, ["wake"])

    assert result.exit_code == 1
    assert "disposition=failed" in result.output


def test_systemd_and_cron_assets_keep_safe_wake_contract() -> None:
    service = (ROOT / "deploy/systemd/helis-scheduler.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy/systemd/helis-scheduler.timer").read_text(encoding="utf-8")
    cron = (ROOT / "deploy/cron/helis-scheduler.cron.example").read_text(encoding="utf-8")

    assert "EnvironmentFile=-%h/.config/helis/helis.env" in service
    assert "helis-scheduler wake" in service
    assert "--minimum-interval-seconds 900" in service
    assert "--lease-seconds 600" in service
    assert "NoNewPrivileges=true" in service
    assert "ReadWritePaths=%h/Helis" in service

    assert "OnUnitActiveSec=5min" in timer
    assert "Persistent=true" in timer

    assert '. "$HOME/.config/helis/helis.env"' in cron
    assert "helis-scheduler\" wake" in cron
    assert "--minimum-interval-seconds 900" in cron
    assert "--lease-seconds 600" in cron


def test_discovery_systemd_and_cron_assets_keep_safe_wake_contract() -> None:
    service = (ROOT / "deploy/systemd/helis-discovery.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy/systemd/helis-discovery.timer").read_text(encoding="utf-8")
    cron = (ROOT / "deploy/cron/helis-discovery.cron.example").read_text(encoding="utf-8")

    assert "EnvironmentFile=-%h/.config/helis/helis.env" in service
    assert "helis-discovery wake" in service
    assert "--config %h/Helis/helis.toml" in service
    assert "--minimum-interval-seconds 3600" in service
    assert "--lease-seconds 900" in service
    assert "--max-model-calls 8" in service
    assert "NoNewPrivileges=true" in service
    assert "ReadWritePaths=%h/Helis" in service

    assert "OnUnitActiveSec=15min" in timer
    assert "Persistent=true" in timer

    assert '. "$HOME/.config/helis/helis.env"' in cron
    assert "helis-discovery\" wake" in cron
    assert "--minimum-interval-seconds 3600" in cron
    assert "--lease-seconds 900" in cron


def test_windows_wake_script_keeps_fixed_bounded_contract_and_literal_env_loading() -> None:
    wake = (ROOT / "deploy/windows/Invoke-HelisWake.ps1").read_text(encoding="utf-8")
    env_loader = (ROOT / "deploy/windows/Import-HelisEnv.ps1").read_text(encoding="utf-8")

    assert 'ValidateSet("Discovery", "Scheduler")' in wake
    assert "Import-HelisEnv.ps1" in wake
    assert "[Environment]::SetEnvironmentVariable" in env_loader
    assert "Invoke-Expression" not in env_loader
    assert "^HELIS_[A-Z0-9_]+$" in env_loader
    assert "Duplicate HELIS environment variable" in env_loader
    assert "--minimum-interval-seconds\", \"3600" in wake
    assert "--lease-seconds\", \"900" in wake
    assert "--max-model-calls\", \"8" in wake
    assert "--max-cost-cents\", \"25" in wake
    assert "--minimum-interval-seconds\", \"900" in wake
    assert "--lease-seconds\", \"600" in wake
    assert "--max-advances\", \"2" in wake
    assert ".venv\\Scripts\\helis-discovery.exe" in wake
    assert ".venv\\Scripts\\helis-scheduler.exe" in wake
    assert '$ErrorActionPreference = "Continue"' in wake
    assert "$exitCode = $LASTEXITCODE" in wake


def test_windows_registration_uses_limited_user_and_keeps_secrets_out_of_action() -> None:
    registration = (ROOT / "deploy/windows/Register-HelisTasks.ps1").read_text(
        encoding="utf-8"
    )

    assert 'Name = "HELIS Discovery"' in registration
    assert 'Name = "HELIS Scheduler"' in registration
    assert "IntervalMinutes = 15" in registration
    assert "IntervalMinutes = 5" in registration
    assert "-LogonType Interactive" in registration
    assert "-RunLevel Limited" in registration
    assert "StartWhenAvailable = $true" in registration
    assert 'MultipleInstances = "IgnoreNew"' in registration
    assert 'if ($Replace)' in registration
    assert 'if ($Disabled)' in registration
    assert '$settingsArguments["Disable"] = $true' in registration
    assert "HELIS_LLM_API_KEY" not in registration
    assert "HELIS_VALIDATION_GATEWAY_TOKEN" not in registration


def test_windows_dashboard_shortcut_is_local_and_contains_no_credentials() -> None:
    installer = (ROOT / "deploy/windows/Install-HelisDashboardShortcut.ps1").read_text(
        encoding="utf-8"
    )
    launcher = (ROOT / "deploy/windows/Start-HelisDashboard.ps1").read_text(encoding="utf-8")

    assert "[System.Environment+SpecialFolder]::DesktopDirectory" in installer
    assert 'ShortcutName = "HELIS Dashboard"' in installer
    assert "WScript.Shell" in installer
    assert "Start-HelisDashboard.ps1" in installer
    assert "-ExecutionPolicy Bypass" in installer
    assert "if ((Test-Path -LiteralPath $shortcutPath) -and (-not $Replace))" in installer
    assert "HELIS_LLM_API_KEY" not in installer
    assert "HELIS_RESEND_API_KEY" not in installer
    assert "HELIS_STRIPE_SECRET_KEY" not in installer

    assert ".venv\\Scripts\\helis-dashboard.exe" in launcher
    assert "serve --port $Port" in launcher
    assert "127.0.0.1" not in launcher
    assert "Start-Process" not in launcher


def test_windows_live_start_is_confirmed_ordered_and_fail_closed() -> None:
    launcher = (ROOT / "deploy/windows/Start-HelisLive.ps1").read_text(encoding="utf-8")

    assert "[switch] $ConfirmLiveOperations" in launcher
    assert "if (-not $ConfirmLiveOperations)" in launcher
    assert '"-Disabled"' in launcher
    assert "if ($ReplaceTasks)" in launcher
    assert "Disable-ScheduledTask -TaskName $taskName" in launcher
    assert "Start-ScheduledTask" not in launcher
    assert "HELIS_VERCEL_TOKEN" not in launcher
    assert "HELIS_STRIPE_SECRET_KEY" not in launcher
    expected_steps = [
        '-Executable $live -Arguments @("bootstrap")',
        '-Executable $live -Arguments @("model-status")',
        '-Executable $live -Arguments @("model-smoke")',
        '-Executable $live -Arguments @("pilot", "--skip-model-probe")',
        '-Executable $live -Arguments @("pilot-status")',
        '-Executable $live -Arguments @("activation-check", "--no-probe-model")',
        '-Executable $discovery -Arguments @("health")',
        '-Executable $scheduler -Arguments @("health")',
        "& $registration @registrationArguments",
        '"--require-schedule"',
        "Enable-ScheduledTask -TaskName $taskName",
        '-Executable $operator -Arguments @("inbox")',
    ]
    positions = [launcher.index(step) for step in expected_steps]
    assert positions == sorted(positions)


def test_env_example_exposes_all_direct_live_adapter_settings_safely() -> None:
    env_example = (ROOT / "deploy/helis.env.example").read_text(encoding="utf-8")

    expected_settings = [
        "HELIS_VERCEL_TOKEN",
        "HELIS_VERCEL_ORG_ID",
        "HELIS_VERCEL_PROJECT_ID",
        "HELIS_VERCEL_CLI",
        "HELIS_BRAVE_SEARCH_API_KEY",
        "HELIS_RESEND_API_KEY",
        "HELIS_RESEND_INBOUND_DOMAIN",
        "HELIS_STRIPE_SECRET_KEY",
    ]
    for setting in expected_settings:
        assert f"# {setting}=" in env_example
    assert '# HELIS_RESEND_FROM="HELIS <hello@your-domain.example>"' in env_example
    assert "HELIS_LLM_REASONING_EFFORT=none" in env_example


def test_windows_controlled_pilot_is_confirmed_ordered_and_fail_closed() -> None:
    pilot = (ROOT / "deploy/windows/Start-HelisControlledPilot.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch] $ConfirmPublicNetworkReads" in pilot
    assert "if (-not $ConfirmPublicNetworkReads)" in pilot
    assert "if ($exitCode -ne 0)" in pilot
    assert "Register-HelisTasks.ps1" not in pilot
    expected_steps = [
        '@("bootstrap")',
        '@("model-status")',
        '@("model-smoke")',
        '@("doctor", "--probe-model")',
        '@("pilot")',
        '@("pilot-status")',
        '@("inbox")',
    ]
    positions = [pilot.index(step) for step in expected_steps]
    assert positions == sorted(positions)


def test_scheduler_health_runs_without_model_or_gateway_calls(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("HELIS_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("HELIS_LLM_MODEL", "qwen3.5:9b")
    monkeypatch.delenv("HELIS_VALIDATION_GATEWAY_URL", raising=False)
    monkeypatch.delenv("HELIS_PROSPECT_GATEWAY_URL", raising=False)
    monkeypatch.delenv("HELIS_CONTACT_GATEWAY_URL", raising=False)

    health(
        db=tmp_path / "helis.db",
        workspace_root=tmp_path / "workspaces",
    )
    output = capsys.readouterr().out

    assert "qwen3.5:9b" in output
    assert "no wake attempts yet" in output
    assert "health check completed" in output


def test_scheduler_health_reports_direct_live_adapter_selection(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("HELIS_VERCEL_TOKEN", "vercel-secret")
    monkeypatch.setenv("HELIS_VERCEL_ORG_ID", "team_helis")
    monkeypatch.setenv("HELIS_VERCEL_PROJECT_ID", "prj_helis")
    monkeypatch.setenv("HELIS_BRAVE_SEARCH_API_KEY", "brave-secret")
    monkeypatch.setenv("HELIS_RESEND_API_KEY", "resend-secret")
    monkeypatch.setenv("HELIS_RESEND_FROM", "HELIS <hello@example.test>")
    monkeypatch.setenv("HELIS_RESEND_INBOUND_DOMAIN", "inbound.resend.app")
    monkeypatch.setenv("HELIS_STRIPE_SECRET_KEY", "stripe-secret")

    health(
        db=tmp_path / "helis.db",
        workspace_root=tmp_path / "workspaces",
    )
    output = capsys.readouterr().out

    assert "vercel_cli_preview_v1" in output
    assert "brave_search_v1" in output
    assert "resend_email_v1" in output
    assert "resend_inbound_results_v1" in output
    assert "stripe_payment_links_v1" in output
    assert "health check completed" in output


def test_discovery_health_parses_config_without_scanning_or_model_calls(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("HELIS_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("HELIS_LLM_MODEL", "qwen3.5:9b")
    config = tmp_path / "helis.toml"
    config.write_text(
        '[[sources]]\nname = "HN Ask"\nkind = "hacker_news"\nfeed = "ask"\nlimit = 10\n',
        encoding="utf-8",
    )

    discovery_health(config=config, db=tmp_path / "helis.db")
    output = capsys.readouterr().out

    assert "qwen3.5:9b" in output
    assert "configured sources" in output
    assert "enabled sources" in output
    assert "no discovery wake attempts yet" in output
    assert "without network/model calls" in output
