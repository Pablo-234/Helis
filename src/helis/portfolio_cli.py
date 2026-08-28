from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from helis.engine import HelisEngine
from helis.portfolio import PortfolioAllocator, PortfolioBudget, PortfolioStore
from helis.portfolio_value import VentureCostEvent, VentureValueEstimator
from helis.resource_envelope import ResourceEnvelope, ResourceEnvelopeManager
from helis.store import HelisStore

app = typer.Typer(help="HELIS portfolio and capital allocation planning")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _print_plan(plan) -> None:
    table = Table("Stage", "Priority", "Expected net/contact", "Cash", "Calls", "Venture")
    candidates = {item.opportunity_id: item for item in plan.candidates}
    for allocation in plan.allocations:
        candidate = candidates[allocation.opportunity_id]
        estimate = candidate.value_estimate
        table.add_row(
            candidate.stage.value,
            f"{allocation.priority_score:.2f}",
            f"{estimate.expected_net_per_next_resolved_contact_cents:.1f}¢",
            f"{allocation.cash_cents} {plan.budget.currency}¢",
            str(allocation.model_calls),
            str(allocation.opportunity_id),
        )
    console.print(table)
    console.print(
        f"allocated: cash={plan.allocated_cash_cents}/{plan.budget.cash_cents} "
        f"{plan.budget.currency}¢ model_calls={plan.allocated_model_calls}/{plan.budget.model_calls}"
    )
    console.print(
        f"[bold]reserve:[/] cash={plan.reserved_cash_cents} {plan.budget.currency}¢ "
        f"model_calls={plan.reserved_model_calls} "
        f"snapshot={plan.snapshot_hash[:12]}…"
    )


def _print_envelopes(items: list[ResourceEnvelope]) -> None:
    table = Table("Status", "Cash remaining", "Calls remaining", "Venture", "Envelope")
    for item in items:
        table.add_row(
            item.status.value,
            f"{item.remaining_cash_cents}/{item.cash_limit_cents} {item.currency}¢",
            f"{item.remaining_model_calls}/{item.model_call_limit}",
            str(item.opportunity_id),
            str(item.id),
        )
    console.print(table)


@app.command()
def plan(
    cash_cents: int = typer.Option(0, min=0),
    currency: str = typer.Option("PLN"),
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
            currency=currency,
            model_calls=model_calls,
            reserve_fraction=reserve_fraction,
            max_ventures=max_ventures,
            max_concentration=max_concentration,
        )
    )
    _print_plan(portfolio)


@app.command()
def activate(db: Path = Path("helis.db")) -> None:
    """Activate resource envelopes from the latest plan and revoke older active envelopes."""
    helis = _engine(db)
    latest = PortfolioStore(helis).latest()
    if latest is None:
        raise typer.BadParameter("no portfolio plan exists")
    items = ResourceEnvelopeManager(helis).activate(latest)
    console.print(f"[green]activated[/] plan={latest.id} envelopes={len(items)}")
    _print_envelopes(items)


@app.command()
def envelopes(db: Path = Path("helis.db")) -> None:
    """List resource envelopes and their remaining capacity."""
    _print_envelopes(ResourceEnvelopeManager(_engine(db)).list())


@app.command("consume-cash")
def consume_cash(
    envelope_id: str,
    amount_cents: int = typer.Option(..., min=1),
    source: str = typer.Option(...),
    idempotency_key: str = typer.Option(...),
    db: Path = Path("helis.db"),
) -> None:
    """Record an actual cash use against one active resource envelope."""
    manager = ResourceEnvelopeManager(_engine(db))
    updated = manager.consume(
        UUID(envelope_id),
        source=source,
        idempotency_key=idempotency_key,
        cash_cents=amount_cents,
    )
    console.print(
        f"cash consumed; remaining={updated.remaining_cash_cents}/{updated.cash_limit_cents} "
        f"{updated.currency}¢"
    )


@app.command()
def revoke(
    envelope_id: str,
    reason: str = typer.Option(...),
    db: Path = Path("helis.db"),
) -> None:
    """Revoke an envelope so no new venture work can consume it."""
    updated = ResourceEnvelopeManager(_engine(db)).revoke(UUID(envelope_id), reason=reason)
    console.print(f"envelope={updated.id} status={updated.status.value}")


@app.command("record-cost")
def record_cost(
    opportunity_id: str,
    amount_cents: int = typer.Option(..., min=1),
    currency: str = typer.Option("PLN"),
    source: str = typer.Option(...),
    external_ref: str | None = typer.Option(None),
    db: Path = Path("helis.db"),
) -> None:
    """Record an actual venture cost outside an active envelope."""
    helis = _engine(db)
    saved = VentureValueEstimator(helis).record_cost(
        VentureCostEvent(
            opportunity_id=UUID(opportunity_id),
            amount_cents=amount_cents,
            currency=currency,
            source=source,
            external_ref=external_ref,
        )
    )
    console.print(
        f"cost={saved.amount_cents} {saved.currency}¢ venture={saved.opportunity_id} "
        f"source={saved.source} id={saved.id}"
    )


@app.command("economics")
def economics(
    opportunity_id: str,
    currency: str = typer.Option("PLN"),
    db: Path = Path("helis.db"),
) -> None:
    """Show the current currency-specific economics estimate for one venture."""
    estimate = VentureValueEstimator(_engine(db)).estimate(UUID(opportunity_id), currency)
    console.print(
        f"venture={estimate.opportunity_id} currency={estimate.currency} "
        f"resolved={estimate.resolved_outcomes} paid_sales={estimate.paid_sales}"
    )
    console.print(
        f"revenue={estimate.observed_revenue_cents}¢ cost={estimate.observed_cost_cents}¢ "
        f"net={estimate.realized_net_cents}¢"
    )
    console.print(
        f"P(paid sale)≈{estimate.posterior_paid_sale_probability:.1%} "
        f"expected_net/next_resolved≈{estimate.expected_net_per_next_resolved_contact_cents:.1f}¢ "
        f"confidence={estimate.evidence_confidence:.1%} uncertainty={estimate.uncertainty:.1%}"
    )


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
