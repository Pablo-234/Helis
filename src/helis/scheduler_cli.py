from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from helis.contact_gateway import ApprovedContactGateway
from helis.engine import HelisEngine
from helis.market_control_loop import MarketAwarePortfolioControlLoop
from helis.market_discovery import (
    MarketDiscoveryMachine,
    MarketDiscoveryPolicy,
    MarketDiscoveryStore,
)
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


def _control_loop(
    helis: HelisEngine,
    workspace_root: Path,
    *,
    market_config: Path,
    market_policy: MarketDiscoveryPolicy,
) -> MarketAwarePortfolioControlLoop:
    provider = OpenAICompatibleProvider.from_env()
    scheduler = PortfolioScheduler(
        helis,
        provider,
        workspace_root=workspace_root,
        validation_gateway=ApprovedValidationGateway.from_env(),
        prospect_gateway=ApprovedProspectGateway.from_env(),
        contact_gateway=ApprovedContactGateway.from_env(),
    )
    portfolio_loop = ReallocatingPortfolioControlLoop(helis, scheduler)
    market = MarketDiscoveryMachine(
        helis,
        provider,
        config_path=market_config,
        policy=market_policy,
    )
    return MarketAwarePortfolioControlLoop(helis, market, portfolio_loop)


def _market_policy(
    scan_interval_seconds: int,
    max_model_calls: int,
    max_tokens: int,
    max_cost_cents: float,
) -> MarketDiscoveryPolicy:
    return MarketDiscoveryPolicy(
        scan_interval_seconds=scan_interval_seconds,
        max_model_calls=max_model_calls,
        max_tokens=max_tokens,
        max_cost_cents=max_cost_cents,
    )


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
    market_scan_interval_seconds: int = typer.Option(21_600, min=60, max=604_800),
    market_max_calls: int = typer.Option(3, min=0, max=20),
    market_max_tokens: int = typer.Option(40_000, min=0, max=500_000),
    market_max_cost_cents: float = typer.Option(10.0, min=0, max=10_000),
    market_config: Path = Path("helis.toml"),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Run bounded market discovery, reconcile capital, then advance funded ventures once."""
    helis = _engine(db)
    loop = _control_loop(
        helis,
        workspace_root,
        market_config=market_config,
        market_policy=_market_policy(
            market_scan_interval_seconds,
            market_max_calls,
            market_max_tokens,
            market_max_cost_cents,
        ),
    )
    _print_report(loop.tick(max_advances=max_advances))


@app.command()
def wake(
    minimum_interval_seconds: int = typer.Option(900, min=0, max=86_400),
    lease_seconds: int = typer.Option(600, min=1, max=86_400),
    max_advances: int = typer.Option(1, min=1, max=20),
    market_scan_interval_seconds: int = typer.Option(21_600, min=60, max=604_800),
    market_max_calls: int = typer.Option(3, min=0, max=20),
    market_max_tokens: int = typer.Option(40_000, min=0, max=500_000),
    market_max_cost_cents: float = typer.Option(10.0, min=0, max=10_000),
    market_config: Path = Path("helis.toml"),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Cron-safe wake: discover markets, reconcile capital and advance bounded funded work."""
    helis = _engine(db)
    loop = _control_loop(
        helis,
        workspace_root,
        market_config=market_config,
        market_policy=_market_policy(
            market_scan_interval_seconds,
            market_max_calls,
            market_max_tokens,
            market_max_cost_cents,
        ),
    )
    result = SchedulerWakeController(helis, loop).wake(
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


@app.command("market-status")
def market_status(db: Path = Path("helis.db")) -> None:
    """Show the latest persisted scheduled market-discovery result."""
    result = MarketDiscoveryStore(_engine(db)).latest()
    if result is None:
        console.print("market discovery: [yellow]no ticks yet[/]")
        return
    console.print(
        f"market={result.scan_disposition.value} scanned={result.scan_fetched} "
        f"new={result.new_observations} processed={result.observations_processed} "
        f"discovered={result.candidates_discovered} evaluated={result.candidates_evaluated}"
    )
    console.print(
        f"usage calls={result.model_calls} tokens={result.tokens} "
        f"cost≈{result.cost_cents:.3f}¢ exhausted={result.budget_exhausted}"
    )
    console.print(f"reason={result.reason}; scan={result.scan_reason}")
    for failure in result.source_failures:
        console.print(f"[yellow]source failed[/] {failure}")


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
    market_config: Path = Path("helis.toml"),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Show local operational readiness without calling models or external gateways."""
    helis = _engine(db)
    provider = OpenAICompatibleProvider.from_env()
    plan = PortfolioStore(helis).latest()
    active = ResourceEnvelopeManager(helis).list(status=EnvelopeStatus.ACTIVE)
    latest_wake = SchedulerWakeStore(helis).latest_result()
    latest_market = MarketDiscoveryStore(helis).latest()

    table = Table("Component", "State")
    table.add_row("database", str(db.expanduser().resolve()))
    table.add_row("workspace", str(workspace_root.expanduser().resolve()))
    table.add_row(
        "market config",
        str(market_config.expanduser().resolve()) if market_config.is_file() else "not found",
    )
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
    table.add_row(
        "last market discovery",
        (
            f"{latest_market.scan_disposition.value} @ {latest_market.created_at.isoformat()}"
            if latest_market
            else "no market discovery ticks yet"
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
