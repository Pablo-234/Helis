from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from helis.autopilot import AutonomousOnlineVentureOperator, AutopilotPolicy
from helis.engine import HelisEngine
from helis.live_gateway_factory import live_gateways_from_env
from helis.model_provider import OpenAICompatibleProvider
from helis.portfolio import PortfolioStore
from helis.source_registry import HelisConfig, SourceKind, SourceRegistry, SourceSpec
from helis.store import HelisStore
from helis.validation_gateway import ApprovedValidationGateway

app = typer.Typer(
    help="HELIS autonomous online-business operator: start with no idea and build toward revenue"
)
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _scanner(config: Path) -> SourceRegistry:
    if config.is_file():
        return SourceRegistry.from_toml(config)
    return SourceRegistry(
        HelisConfig(
            sources=[
                SourceSpec(
                    name="hacker-news-ask",
                    kind=SourceKind.HACKER_NEWS,
                    feed="ask",
                    limit=60,
                )
            ]
        )
    )


def _operator(
    engine: HelisEngine,
    config: Path,
    workspace_root: Path,
) -> AutonomousOnlineVentureOperator:
    live = live_gateways_from_env()
    return AutonomousOnlineVentureOperator(
        engine,
        OpenAICompatibleProvider.from_env(),
        lambda: _scanner(config),
        workspace_root=workspace_root,
        validation_gateway=ApprovedValidationGateway.from_env(),
        preview_gateway=live.preview,
        prospect_gateway=live.prospect,
        contact_gateway=live.contact,
        contact_result_gateway=live.contact_result,
        commerce_gateway=live.commerce,
    )


def _print_ventures(engine: HelisEngine) -> None:
    ventures = [
        item
        for item in engine.store.list_opportunities()
        if item.business_model is not None and "online_venture" in item.tags
    ]
    if not ventures:
        console.print("online ventures: [yellow]none persisted[/]")
        return

    table = Table("Stage", "Model", "Delivery", "Revenue model", "Score", "Venture")
    for item in sorted(ventures, key=lambda venture: (venture.stage.value, venture.title)):
        model = item.business_model
        if model is None:
            continue
        table.add_row(
            item.stage.value,
            model.name,
            model.delivery_model.value,
            model.revenue_model.value,
            f"{item.business_model_score:.1f}" if item.business_model_score is not None else "-",
            str(item.id),
        )
    console.print(table)


def _run(
    *,
    config: Path,
    db: Path,
    workspace_root: Path,
    cash_cents: int,
    currency: str,
    portfolio_model_calls: int,
    discovery_model_calls: int,
    max_ventures: int,
    max_rounds: int,
    max_advances_per_round: int,
    live_auto: bool,
    auto_checkout: bool,
    auto_publication: bool,
    auto_first_contact: bool,
) -> None:
    engine = _engine(db)
    live = live_gateways_from_env()
    configured = ", ".join(
        f"{key}={value or 'missing'}" for key, value in live.names.items()
    )
    console.print(f"live adapters: {configured}")
    policy = AutopilotPolicy(
        cash_cents=cash_cents,
        currency=currency,
        portfolio_model_calls=portfolio_model_calls,
        discovery_model_calls=discovery_model_calls,
        max_ventures=max_ventures,
        max_rounds=max_rounds,
        max_advances_per_round=max_advances_per_round,
        allow_checkout_without_approval=live_auto or auto_checkout,
        allow_publication_without_approval=live_auto or auto_publication,
        allow_first_contact_without_approval=live_auto or auto_first_contact,
    )
    grants = ", ".join(policy.granted_live_actions) or "none"
    console.print(f"run-scoped autonomy grants: {grants}; autonomous spend=0¢")
    report = _operator(engine, config, workspace_root).run(policy)

    discovery = report.discovery
    console.print("[bold green]HELIS ZERO-TO-REVENUE RUN COMPLETE[/]")
    console.print(
        "discovery: "
        f"fetched={discovery.observations_fetched} new={discovery.observations_new} "
        f"used={discovery.observations_used} discovered={discovery.candidates_discovered} "
        f"evaluated={discovery.candidates_evaluated} experiments={discovery.experiments_planned}"
    )
    console.print(
        f"portfolio={report.portfolio_plan_id or '-'} funded_ventures={report.funded_ventures} "
        f"revenue={report.revenue_cents} {report.currency}¢"
    )
    for index, tick in enumerate(report.scheduler_rounds, start=1):
        console.print(
            f"round {index}: attempted={tick.attempted_advances}/{tick.max_advances} "
            f"advanced={tick.advanced} noop={tick.noop} skipped={tick.skipped} failed={tick.failed}"
        )
        for item in tick.items:
            console.print(
                f"  {item.disposition.value}: venture={item.opportunity_id} reason={item.reason}"
            )
    console.print(f"stop={report.stop_reason.value}")
    if report.blockers:
        console.print("next real-world gates:")
        for blocker in report.blockers:
            console.print(f"  - {blocker}")
    console.print(f"stages={report.stage_counts}")
    _print_ventures(engine)


def _common_run(
    *,
    config: Path,
    db: Path,
    workspace_root: Path,
    cash_cents: int,
    currency: str,
    portfolio_model_calls: int,
    discovery_model_calls: int,
    max_ventures: int,
    max_rounds: int,
    max_advances_per_round: int,
    live_auto: bool,
    auto_checkout: bool,
    auto_publication: bool,
    auto_first_contact: bool,
) -> None:
    _run(
        config=config,
        db=db,
        workspace_root=workspace_root,
        cash_cents=cash_cents,
        currency=currency,
        portfolio_model_calls=portfolio_model_calls,
        discovery_model_calls=discovery_model_calls,
        max_ventures=max_ventures,
        max_rounds=max_rounds,
        max_advances_per_round=max_advances_per_round,
        live_auto=live_auto,
        auto_checkout=auto_checkout,
        auto_publication=auto_publication,
        auto_first_contact=auto_first_contact,
    )


@app.command()
def start(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    cash_cents: int = typer.Option(0, min=0),
    currency: str = typer.Option("PLN", min=3, max=3),
    portfolio_model_calls: int = typer.Option(80, min=1, max=10_000),
    discovery_model_calls: int = typer.Option(8, min=1, max=100),
    max_ventures: int = typer.Option(3, min=1, max=20),
    max_rounds: int = typer.Option(12, min=1, max=100),
    max_advances_per_round: int = typer.Option(3, min=1, max=20),
    live_auto: bool = typer.Option(
        False,
        "--live-auto",
        help="Grant checkout creation, publication and first-contact autonomy for this run only.",
    ),
    auto_checkout: bool = typer.Option(False, "--auto-checkout"),
    auto_publication: bool = typer.Option(False, "--auto-publication"),
    auto_first_contact: bool = typer.Option(False, "--auto-first-contact"),
) -> None:
    """Start from a blank HELIS database and advance online ventures toward real revenue."""
    _common_run(
        config=config,
        db=db,
        workspace_root=workspace_root,
        cash_cents=cash_cents,
        currency=currency,
        portfolio_model_calls=portfolio_model_calls,
        discovery_model_calls=discovery_model_calls,
        max_ventures=max_ventures,
        max_rounds=max_rounds,
        max_advances_per_round=max_advances_per_round,
        live_auto=live_auto,
        auto_checkout=auto_checkout,
        auto_publication=auto_publication,
        auto_first_contact=auto_first_contact,
    )


@app.command()
def run(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    cash_cents: int = typer.Option(0, min=0),
    currency: str = typer.Option("PLN", min=3, max=3),
    portfolio_model_calls: int = typer.Option(80, min=1, max=10_000),
    discovery_model_calls: int = typer.Option(8, min=1, max=100),
    max_ventures: int = typer.Option(3, min=1, max=20),
    max_rounds: int = typer.Option(12, min=1, max=100),
    max_advances_per_round: int = typer.Option(3, min=1, max=20),
    live_auto: bool = typer.Option(False, "--live-auto"),
    auto_checkout: bool = typer.Option(False, "--auto-checkout"),
    auto_publication: bool = typer.Option(False, "--auto-publication"),
    auto_first_contact: bool = typer.Option(False, "--auto-first-contact"),
) -> None:
    """Backward-compatible alias for start."""
    _common_run(
        config=config,
        db=db,
        workspace_root=workspace_root,
        cash_cents=cash_cents,
        currency=currency,
        portfolio_model_calls=portfolio_model_calls,
        discovery_model_calls=discovery_model_calls,
        max_ventures=max_ventures,
        max_rounds=max_rounds,
        max_advances_per_round=max_advances_per_round,
        live_auto=live_auto,
        auto_checkout=auto_checkout,
        auto_publication=auto_publication,
        auto_first_contact=auto_first_contact,
    )


@app.command()
def status(db: Path = Path("helis.db")) -> None:
    """Show current autonomous online-venture state without network or model calls."""
    engine = _engine(db)
    plan = PortfolioStore(engine).latest()
    live = live_gateways_from_env()
    console.print(
        "live adapters: "
        + ", ".join(f"{key}={value or 'missing'}" for key, value in live.names.items())
    )
    console.print(f"latest portfolio={plan.id if plan else '-'}")
    if plan is not None:
        console.print(
            f"treasury={plan.budget.cash_cents} {plan.budget.currency}¢ "
            f"model_calls={plan.budget.model_calls} allocations={len(plan.allocations)}"
        )
    _print_ventures(engine)
    console.print("[green]status completed without network/model calls[/]")


if __name__ == "__main__":
    app()
