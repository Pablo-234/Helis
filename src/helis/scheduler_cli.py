from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.portfolio_scheduler import PortfolioScheduler, SchedulerStore, SchedulerTickReport
from helis.store import HelisStore
from helis.validation_gateway import ApprovedValidationGateway

app = typer.Typer(help="HELIS bounded portfolio execution scheduler")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


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
        f"advanced={report.advanced} skipped={report.skipped} failed={report.failed}"
    )


@app.command()
def tick(
    max_advances: int = typer.Option(1, min=1, max=20),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Advance the highest-priority eligible active venture envelopes once."""
    helis = _engine(db)
    scheduler = PortfolioScheduler(
        helis,
        OpenAICompatibleProvider.from_env(),
        workspace_root=workspace_root,
        validation_gateway=ApprovedValidationGateway.from_env(),
    )
    _print_report(scheduler.tick(max_advances=max_advances))


@app.command()
def status(db: Path = Path("helis.db")) -> None:
    """Show the most recently persisted scheduler tick."""
    helis = _engine(db)
    report = SchedulerStore(helis).latest()
    if report is None:
        console.print("scheduler: [yellow]no ticks yet[/]")
        return
    _print_report(report)


if __name__ == "__main__":
    app()
