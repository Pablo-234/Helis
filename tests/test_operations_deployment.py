from __future__ import annotations

from pathlib import Path

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
