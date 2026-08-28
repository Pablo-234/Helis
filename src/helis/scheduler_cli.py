from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from helis.contact_gateway import ApprovedContactGateway
from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.portfolio import PortfolioStore
from helis.portfolio_reallocation import ReallocatingPortfolioControlLoop
from helis.portfolio_scheduler import PortfolioScheduler, SchedulerStore, SchedulerTickReport
from helis.prospect_gateway import ApprovedProspectGateway
from helis.resource_envelope import EnvelopeStatus, ResourceEnvelopeManager
from helis.scheduler_wake import SchedulerWakeController, SchedulerWakeStore, WakePolicy
from helis.store import HelisStore
from helis.validation_gateway import ApprovedValidationGateway

app = typer.Typer(help="HELIS bounded portfolio execution scheduler")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _control_loop(helis: HelisEngine, workspace_root: Path) -> ReallocatingPortfolioControlLoop:
    provider = OpenAICompatibleProvider.from_env()
    scheduler = PortfolioScheduler(
        helis,
        provider,
        workspace_root=workspace_root,
        validation_gateway=ApprovedValidationGateway.from_env(),
        prospect_gateway=ApprovedProspectGateway.from_env(),
        contact_gateway=ApprovedContactGateway.from_env(),
    )
    return ReallocatingPortfolioControlLoop(helis, scheduler)


def _print_report(report: SchedulerTickReport) -> None:
    table = Table("Disposition", "Priority", "Reason", "Calls", "Cash available", "Venture")
    for item in report.items:
        table.add_row(
            item.disposition.value,
            f"{item.priority_score:.2f}",
            item.reason,
            f"{item.model_calls_before}→{item.model_calls_after}",
            f"{item.available_cash_before}→{item.available_cash_after}",
            str(item.opportunity_id),
        )
    console.print(table)
    console.print(
        f"scheduler tick={report.id} plan={report.plan_id or '-'} "
        f"attempted={report.attempted_advances}/{report.max_advances} "
        f"advanced={report.advanced} noop={report.noop} "
        f"skipped={report.skipped} failed={report.failed}"
    )


def _gateway_status(factory) -> str:
    try:
        gateway = factory()
    except ValueError as exc:
        return f"[red]invalid: {exc}[/]"
    if gateway is None:
        return "[yellow]not configured[/]"
    return f"[green]{gateway.safe_destination}[/]"


@app.command()
def tick(
    max_advances: int = typer.Option(1, min=1, max=20),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Reconcile remaining capital, then advance eligible funded ventures once."""
    helis = _engine(db)
    _print_report(_control_loop(helis, workspace_root).tick(max_advances=max_advances))


@app.command()
def wake(
    minimum_interval_seconds: int = typer.Option(900, min=0, max=86_400),
    lease_seconds: int = typer.Option(600, min=1, max=86_400),
    max_advances: int = typer.Option(1, min=1, max=20),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Cron-safe wake: reconcile capital and tick only when due and no lease is active."""
    helis = _engine(db)
    result = SchedulerWakeController(
        helis,
        _control_loop(helis, workspace_root),
    ).wake(
        WakePolicy(
            minimum_interval_seconds=minimum_interval_seconds,
            lease_seconds=lease_seconds,
            max_advances=max_advances,
        )
    )
    console.print(
        f"wake={result.disposition.value} reason={result.reason} "
        f"report={result.scheduler_report_id or '-'} owner={result.owner_id or '-'}"
    )


@app.command("wake-status")
def wake_status(db: Path = Path("helis.db")) -> None:
    """Show the latest wake decision, including throttled and lease-blocked attempts."""
    result = SchedulerWakeStore(_engine(db)).latest_result()
    if result is None:
        console.print("scheduler wake: [yellow]no attempts yet[/]")
        return
    console.print(
        f"wake={result.disposition.value} attempted={result.attempted_at.isoformat()} "
        f"completed={result.completed_at.isoformat() if result.completed_at else '-'}"
    )
    console.print(
        f"reason={result.reason} report={result.scheduler_report_id or '-'} "
        f"owner={result.owner_id or '-'}"
    )


@app.command()
def status(db: Path = Path("helis.db")) -> None:
    """Show the most recently persisted scheduler tick."""
    helis = _engine(db)
    report = SchedulerStore(helis).latest()
    if report is None:
        console.print("scheduler: [yellow]no ticks yet[/]")
        return
    _print_report(report)


@app.command()
def health(
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Show local operational readiness without calling models or external gateways."""
    helis = _engine(db)
    provider = OpenAICompatibleProvider.from_env()
    plan = PortfolioStore(helis).latest()
    active = ResourceEnvelopeManager(helis).list(status=EnvelopeStatus.ACTIVE)
    latest_wake = SchedulerWakeStore(helis).latest_result()

    table = Table("Component", "State")
    table.add_row("database", str(db.expanduser().resolve()))
    table.add_row("workspace", str(workspace_root.expanduser().resolve()))
    table.add_row("LLM endpoint", provider.base_url)
    table.add_row("LLM model", provider.model)
    table.add_row("latest portfolio plan", str(plan.id) if plan else "not created")
    table.add_row("active resource envelopes", str(len(active)))
    table.add_row(
        "last scheduler wake",
        (
            f"{latest_wake.disposition.value} @ {latest_wake.attempted_at.isoformat()}"
            if latest_wake
            else "no wake attempts yet"
        ),
    )
    table.add_row("validation gateway", _gateway_status(ApprovedValidationGateway.from_env))
    table.add_row("prospect gateway", _gateway_status(ApprovedProspectGateway.from_env))
    table.add_row("contact gateway", _gateway_status(ApprovedContactGateway.from_env))
    console.print(table)
    console.print(
        "[green]health check completed[/] — no model call, external request, approval or spend occurred"
    )


if __name__ == "__main__":
    app()
