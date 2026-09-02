from __future__ import annotations

from pathlib import Path

import pytest
import typer

import helis.live_cli as live_module
from helis.live_activation import LiveActivationCheck, LiveActivationReport
from helis.live_readiness import (
    LiveReadinessReport,
    ReadinessCheck,
    ReadinessLevel,
)
from helis.local_model_runtime import LocalModelReport, LocalModelState


def _model_report(state: LocalModelState) -> LocalModelReport:
    ready = state == LocalModelState.READY
    return LocalModelReport(
        state=state,
        endpoint="http://localhost:11434/v1",
        configured_model="qwen3.5:9b",
        endpoint_reachable=ready,
        model_available=ready,
        detail="configured model is present" if ready else "local endpoint is unavailable",
        next_command="run helis-live model-smoke" if ready else "ollama serve",
    )


@pytest.mark.parametrize("json_output", [False, True])
def test_model_status_returns_nonzero_when_blocked(monkeypatch, json_output: bool) -> None:
    monkeypatch.setattr(
        live_module.LocalModelInspector,
        "inspect",
        lambda self: _model_report(LocalModelState.ENDPOINT_DOWN),
    )

    with pytest.raises(typer.Exit) as captured:
        live_module.model_status(json_output=json_output)

    assert captured.value.exit_code == 1


def test_model_status_returns_success_when_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        live_module.LocalModelInspector,
        "inspect",
        lambda self: _model_report(LocalModelState.READY),
    )

    live_module.model_status(json_output=True)


def _readiness_report(*, ready: bool) -> LiveReadinessReport:
    return LiveReadinessReport(
        checks=[
            ReadinessCheck(
                key="model",
                label="Local model",
                level=ReadinessLevel.READY if ready else ReadinessLevel.BLOCKED,
                detail="configured model is present" if ready else "local endpoint is unavailable",
                required_for_pilot=True,
            )
        ],
        pilot_ready=ready,
    )


def _patch_readiness(monkeypatch, *, ready: bool) -> None:
    report = _readiness_report(ready=ready)

    class FakeInspector:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def inspect(self, *, probe_model: bool) -> LiveReadinessReport:
            return report

    monkeypatch.setattr(live_module, "LiveReadinessInspector", FakeInspector)


@pytest.mark.parametrize("json_output", [False, True])
def test_doctor_returns_nonzero_when_pilot_is_blocked(
    tmp_path: Path,
    monkeypatch,
    json_output: bool,
) -> None:
    _patch_readiness(monkeypatch, ready=False)

    with pytest.raises(typer.Exit) as captured:
        live_module.doctor(
            config=tmp_path / "helis.toml",
            db=tmp_path / "helis.db",
            workspace_root=tmp_path / "workspaces",
            self_improvement_root=tmp_path / "self-improvement",
            probe_model=False,
            json_output=json_output,
        )

    assert captured.value.exit_code == 1


def test_doctor_returns_success_when_pilot_is_ready(tmp_path: Path, monkeypatch) -> None:
    _patch_readiness(monkeypatch, ready=True)

    live_module.doctor(
        config=tmp_path / "helis.toml",
        db=tmp_path / "helis.db",
        workspace_root=tmp_path / "workspaces",
        self_improvement_root=tmp_path / "self-improvement",
        probe_model=False,
        json_output=True,
    )


def test_activation_check_returns_nonzero_when_live_path_is_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = LiveActivationReport(
        checks=[
            LiveActivationCheck(
                key="gateway_contact",
                label="First contact",
                level=ReadinessLevel.BLOCKED,
                detail="contact adapter is missing or incomplete",
            )
        ],
        activation_ready=False,
    )

    class FakeInspector:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def inspect(self, *, probe_model: bool, require_schedule: bool):
            assert probe_model is False
            assert require_schedule is True
            return report

    monkeypatch.setattr(live_module, "LiveActivationInspector", FakeInspector)

    with pytest.raises(typer.Exit) as captured:
        live_module.activation_check(
            config=tmp_path / "helis.toml",
            db=tmp_path / "helis.db",
            workspace_root=tmp_path / "workspaces",
            self_improvement_root=tmp_path / "self-improvement",
            probe_model=False,
            require_schedule=True,
            json_output=True,
        )

    assert captured.value.exit_code == 1
