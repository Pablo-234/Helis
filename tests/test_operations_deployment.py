from __future__ import annotations

from pathlib import Path

from helis.discovery_cli import health as discovery_health
from helis.scheduler_cli import health

ROOT = Path(__file__).resolve().parents[1]


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
    assert "-MultipleInstances IgnoreNew" in registration
    assert "-StartWhenAvailable" in registration
    assert 'if ($Replace)' in registration
    assert "HELIS_LLM_API_KEY" not in registration
    assert "HELIS_VALIDATION_GATEWAY_TOKEN" not in registration


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
