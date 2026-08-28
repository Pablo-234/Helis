from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from helis.engine import HelisEngine
from helis.portfolio import PortfolioAllocator, PortfolioBudget, PortfolioStore
from helis.store import HelisStore

app = typer.Typer(help="HELIS portfolio and capital allocation planning")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _print_plan(plan) -> None:
    table = Table("Stage", "Priority", "Cash", "Model calls", "Venture")
    candidates = {item.opportunity_id: item for item in plan.candidates}
    for allocation in plan.allocations:
        candidate = candidates[allocation.opportunity_id]
        table.add_row(
            candidate.stage.value,
            f"{allocation.priority_score:.2f}",
            str(allocation.cash_cents),
            str(allocation.model_calls),
            str(allocation.opportunity_id),
        )
    console.print(table)
    console.print(
        f"allocated: cash={plan.allocated_cash_cents}/{plan.budget.cash_cents}¢ "
        f"model_calls={plan.allocated_model_calls}/{plan.budget.model_calls}"
    )
    console.print(
        f"[bold]reserve:[/] cash={plan.reserved_cash_cents}¢ "
        f"model_calls={plan.reserved_model_calls} "
        f"snapshot={plan.snapshot_hash[:12]}…"
    )


@app.command()
def plan(
    cash_cents: int = typer.Option(0, min=0),
    model_calls: int = typer.Option(0, min=0),
    reserve_fraction: float = typer.Option(0.20, min=0, max=0.90),
    max_ventures: int = typer.Option(4, min=1, max=50),
    max_concentration: float = typer.Option(0.60, min=0.01, max=1.0),
    db: Path = Path("helis.db"),
) -> None:
    """Create an auditable allocation plan. This command does not spend or execute resources."""
    portfolio = PortfolioAllocator(_engine(db)).plan(
        PortfolioBudget(
            cash_cents=cash_cents,
            model_calls=model_calls,
            reserve_fraction=reserve_fraction,
            max_ventures=max_ventures,
            max_concentration=max_concentration,
        )
    )
    _print_plan(portfolio)


@app.command()
def status(db: Path = Path("helis.db")) -> None:
    """Show the most recently persisted resource allocation plan."""
    helis = _engine(db)
    latest = PortfolioStore(helis).latest()
    if latest is None:
        console.print("portfolio: [yellow]no plan yet[/]")
        return
    _print_plan(latest)


if __name__ == "__main__":
    app()
