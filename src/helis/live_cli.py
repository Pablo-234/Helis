from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from helis.autopilot import AutopilotPolicy
from helis.engine import HelisEngine
from helis.live_gateway_factory import live_gateways_from_env
from helis.live_readiness import (
    LiveBootstrapper,
    LivePilotFailure,
    LivePilotReport,
    LivePilotRunner,
    LivePilotStore,
    LiveReadinessInspector,
    ReadinessLevel,
)
from helis.local_model_runtime import LocalModelInspector, LocalModelState
from helis.model_provider import OpenAICompatibleProvider
from helis.source_registry import SourceRegistry
from helis.store import HelisStore

app = typer.Typer(help="Inspect HELIS readiness for a real zero-to-revenue internet run")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


@app.command("model-status")
def model_status(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Verify the local runtime and exact configured model without requesting a completion."""
    report = LocalModelInspector(OpenAICompatibleProvider.from_env()).inspect()
    if json_output:
        console.print_json(json.dumps(report.model_dump(mode="json")))
    else:
        table = Table("Item", "Value")
        table.add_row("state", report.state.value)
        table.add_row("endpoint", Text(report.endpoint))
        table.add_row("configured model", Text(report.configured_model))
        table.add_row("endpoint reachable", str(report.endpoint_reachable).lower())
        table.add_row("model available", str(report.model_available).lower())
        table.add_row("Ollama CLI", Text(report.ollama_cli or "not found"))
        table.add_row("detail", Text(report.detail))
        table.add_row("next", Text(report.next_command))
        console.print(table)
        if report.state == LocalModelState.READY:
            console.print("local model inventory: READY", style="bold green")
        else:
            console.print("local model inventory: BLOCKED", style="bold red")
    if report.state != LocalModelState.READY:
        raise typer.Exit(code=1)


@app.command("model-smoke")
def model_smoke(
    timeout_seconds: float = typer.Option(60.0, min=5.0, max=300.0),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Request one capped localhost completion and verify its JSON contract."""
    report = LocalModelInspector(OpenAICompatibleProvider.from_env()).smoke(
        timeout_seconds=timeout_seconds
    )
    if json_output:
        console.print_json(json.dumps(report.model_dump(mode="json")))
    else:
        style = "bold green" if report.success else "bold red"
        console.print(
            f"local model smoke: {'PASS' if report.success else 'FAIL'}",
            style=style,
        )
        console.print(
            f"model={report.model} endpoint={report.endpoint} "
            f"latency={report.latency_seconds:.3f}s status={report.response_status or '-'}",
            markup=False,
        )
        if report.error:
            console.print(f"error={report.error}", markup=False)
    if not report.success:
        raise typer.Exit(code=1)


def _print_pilot(report: LivePilotReport) -> None:
    if report.autopilot is None:
        console.print("HELIS CONTROLLED PILOT FAILED", style="bold red")
        console.print(f"pilot={report.id} error={report.error or '-'}", markup=False)
        console.print(
            f"cash_limit={report.cash_limit_cents} "
            f"external_write_gateways={report.external_write_gateways_enabled}",
            markup=False,
        )
        return
    discovery = report.autopilot.discovery
    console.print("HELIS CONTROLLED PILOT COMPLETE", style="bold green")
    console.print(
        f"pilot={report.id} stop={report.autopilot.stop_reason.value} "
        f"cash_limit={report.cash_limit_cents} external_write_gateways="
        f"{report.external_write_gateways_enabled}",
        markup=False,
    )
    console.print(
        f"discovery: fetched={discovery.observations_fetched} "
        f"new={discovery.observations_new} used={discovery.observations_used} "
        f"discovered={discovery.candidates_discovered} "
        f"evaluated={discovery.candidates_evaluated} "
        f"experiments={discovery.experiments_planned}",
        markup=False,
    )
    console.print(
        f"portfolio={report.autopilot.portfolio_plan_id or '-'} "
        f"funded={report.autopilot.funded_ventures} "
        f"advanced={report.autopilot.total_advanced} "
        f"operator_items={len(report.operator_items)}",
        markup=False,
    )
    if report.autopilot.blockers:
        console.print("next gates:")
        for blocker in report.autopilot.blockers:
            console.print(f"  - {blocker}", markup=False)
    if report.operator_items:
        table = Table("Priority", "Type", "Kind", "Venture", "Next command")
        for item in report.operator_items:
            table.add_row(
                str(item.priority),
                item.request_type.value,
                item.kind.value,
                Text(item.venture_title),
                Text(item.action_command),
            )
        console.print(table)
    else:
        console.print("operator inbox: no unresolved items")


@app.command()
def bootstrap(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    self_improvement_root: Path = Path(".helis/self-improvement"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Create safe local directories, source config and SQLite schema without overwriting files."""
    report = LiveBootstrapper(
        config=config,
        db=db,
        workspace_root=workspace_root,
        self_improvement_root=self_improvement_root,
    ).run()
    if json_output:
        console.print_json(json.dumps(report.model_dump(mode="json")))
        return
    table = Table("Item", "Path", "State")
    table.add_row(
        "market config",
        str(report.config),
        "created" if report.config_created else "preserved",
    )
    table.add_row(
        "database",
        str(report.database),
        "created" if report.database_created else "preserved",
    )
    table.add_row("venture workspace", str(report.workspace_root), "ready")
    table.add_row("self-improvement workspace", str(report.self_improvement_root), "ready")
    console.print(table)
    console.print("bootstrap completed without overwriting existing configuration")


@app.command()
def doctor(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    self_improvement_root: Path = Path(".helis/self-improvement"),
    probe_model: bool = typer.Option(
        False,
        "--probe-model/--no-probe-model",
        help="GET local /models metadata; never request a completion",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Inspect pilot readiness; optional probe performs one uncredentialed localhost read."""
    report = LiveReadinessInspector(
        OpenAICompatibleProvider.from_env(),
        config=config,
        db=db,
        workspace_root=workspace_root,
        self_improvement_root=self_improvement_root,
    ).inspect(probe_model=probe_model)
    if json_output:
        console.print_json(json.dumps(report.model_dump(mode="json")))
    else:
        table = Table("Component", "State", "Required", "Detail")
        styles = {
            ReadinessLevel.READY: "green",
            ReadinessLevel.WARNING: "yellow",
            ReadinessLevel.BLOCKED: "red",
        }
        for item in report.checks:
            table.add_row(
                item.label,
                Text(item.level.value, style=styles[item.level]),
                "pilot" if item.required_for_pilot else "optional",
                Text(item.detail),
            )
        console.print(table)
        if report.pilot_ready:
            console.print("pilot readiness: READY", style="bold green")
        else:
            console.print("pilot readiness: BLOCKED", style="bold red")
            for item in report.blocking:
                console.print(f"  - {item.label}: {item.detail}", markup=False)
    if not report.pilot_ready:
        raise typer.Exit(code=1)


@app.command()
def pilot(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    self_improvement_root: Path = Path(".helis/self-improvement"),
    discovery_model_calls: int = typer.Option(8, min=1, max=20),
    portfolio_model_calls: int = typer.Option(24, min=4, max=100),
    max_rounds: int = typer.Option(4, min=1, max=12),
    probe_model: bool = typer.Option(
        True,
        "--probe-model/--skip-model-probe",
        help="Require the local metadata endpoint before starting",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Run one bounded zero-cash pilot with every external-write gateway disabled."""
    LiveBootstrapper(
        config=config,
        db=db,
        workspace_root=workspace_root,
        self_improvement_root=self_improvement_root,
    ).run()
    provider = OpenAICompatibleProvider.from_env()
    readiness = LiveReadinessInspector(
        provider,
        config=config,
        db=db,
        workspace_root=workspace_root,
        self_improvement_root=self_improvement_root,
    ).inspect(probe_model=probe_model)
    if not readiness.pilot_ready:
        reasons = "; ".join(f"{item.label}: {item.detail}" for item in readiness.blocking)
        raise typer.BadParameter(f"pilot preflight failed: {reasons}")
    helis = _engine(db)
    try:
        report = LivePilotRunner(
            helis,
            provider,
            lambda: SourceRegistry.from_toml(config),
            workspace_root=workspace_root,
        ).run(
            AutopilotPolicy(
                cash_cents=0,
                reserve_fraction=0,
                max_ventures=1,
                discovery_model_calls=discovery_model_calls,
                discovery_max_cost_cents=0,
                portfolio_model_calls=portfolio_model_calls,
                max_rounds=max_rounds,
                max_advances_per_round=1,
            )
        )
    except LivePilotFailure as exc:
        if json_output:
            console.print_json(json.dumps(exc.report.model_dump(mode="json")))
        else:
            _print_pilot(exc.report)
        raise typer.Exit(code=1) from exc
    if json_output:
        console.print_json(json.dumps(report.model_dump(mode="json")))
        return
    _print_pilot(report)


@app.command("pilot-status")
def pilot_status(
    db: Path = Path("helis.db"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show the latest persisted controlled-pilot report without network or model calls."""
    report = LivePilotStore(_engine(db)).latest()
    if report is None:
        console.print("no controlled pilot has completed")
        return
    if json_output:
        console.print_json(json.dumps(report.model_dump(mode="json")))
        return
    _print_pilot(report)


@app.command("env-example")
def env_example() -> None:
    """Print the direct-adapter environment variable names without exposing any secret values."""
    console.print(
        '''# Local/OpenAI-compatible model
$env:HELIS_LLM_BASE_URL="http://localhost:11434/v1"
$env:HELIS_LLM_MODEL="qwen3.5:9b"

# Vercel preview publication
$env:HELIS_VERCEL_TOKEN="<secret>"
$env:HELIS_VERCEL_ORG_ID="<org-id>"
$env:HELIS_VERCEL_PROJECT_ID="<project-id>"

# Brave Search prospecting
$env:HELIS_BRAVE_SEARCH_API_KEY="<secret>"
$env:HELIS_BRAVE_COUNTRY="PL"
$env:HELIS_BRAVE_SEARCH_LANG="pl"

# Resend outbound + inbound replies
$env:HELIS_RESEND_API_KEY="<secret>"
$env:HELIS_RESEND_FROM="HELIS <hello@your-domain.example>"
$env:HELIS_RESEND_INBOUND_DOMAIN="your-inbound.resend.app"

# Stripe self-serve checkout
$env:HELIS_STRIPE_SECRET_KEY="<secret>"'''
    )


@app.command("selected")
def selected() -> None:
    """Show adapter selection only; makes no network requests."""
    live = live_gateways_from_env()
    for key, name in live.names.items():
        console.print(f"{key}: {name or 'missing'}")
    if os.getenv("HELIS_PREVIEW_GATEWAY_URL") and live.names["preview"] != "approved_preview_gateway_v1":
        console.print("[dim]direct preview adapter takes precedence over generic preview gateway[/]")


if __name__ == "__main__":
    app()
