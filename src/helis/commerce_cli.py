from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from helis.commerce_gateway import ApprovedCommerceGateway
from helis.commerce_manager import CommerceError, CommerceManager
from helis.engine import HelisEngine
from helis.store import HelisStore

app = typer.Typer(help="Inspect, approve and observe HELIS self-serve commerce")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _manager(db: Path, *, with_gateway: bool = False) -> CommerceManager:
    gateway = ApprovedCommerceGateway.from_env() if with_gateway else None
    return CommerceManager(_engine(db), gateway=gateway)


@app.command()
def status(
    db: Path = Path("helis.db"),
    opportunity_id: str | None = None,
) -> None:
    """List checkout runs without network or model calls."""
    manager = _manager(db)
    target = UUID(opportunity_id) if opportunity_id else None
    runs = manager.state.list_runs(target)
    table = Table("Status", "Approved", "Price", "Checkout", "Venture", "Run")
    for run in runs:
        offer = manager.state.get_offer(run.offer_id)
        binding = manager.state.get_binding_for_run(run.id)
        table.add_row(
            run.status.value,
            "yes" if run.approval_granted else "no",
            offer.display_price if offer else "-",
            binding.checkout_url if binding else "-",
            str(run.opportunity_id),
            str(run.id),
        )
    console.print(table)
    if not runs:
        console.print("[yellow]no self-serve checkout runs[/]")


@app.command()
def approve(run_id: str, db: Path = Path("helis.db")) -> None:
    """Approve exactly one persisted checkout run and its immutable offer hash."""
    try:
        run = _manager(db).approve(UUID(run_id))
    except CommerceError as exc:
        raise typer.BadParameter(str(exc)) from exc
    offer = _manager(db).state.get_offer(run.offer_id)
    console.print(
        f"[green]approved[/] run={run.id} offer={run.offer_hash[:12]}… "
        f"price={offer.display_price if offer else '-'}"
    )


@app.command()
def activate(run_id: str, db: Path = Path("helis.db")) -> None:
    """Create the approved checkout through the configured commerce gateway."""
    manager = _manager(db, with_gateway=True)
    run = manager.state.get_run(UUID(run_id))
    if run is None:
        raise typer.BadParameter("checkout run not found")
    report = manager.advance_prebuild(run.opportunity_id)
    console.print(f"commerce={report.reason}")
    if report.binding is not None:
        console.print(
            f"[bold green]checkout active[/] {report.binding.checkout_url} "
            f"offer={report.binding.offer_hash[:12]}…"
        )


@app.command()
def poll(opportunity_id: str, db: Path = Path("helis.db")) -> None:
    """Poll observed payment state without using the model."""
    manager = _manager(db, with_gateway=True)
    report = manager.poll_payment(UUID(opportunity_id))
    console.print(f"commerce={report.reason}")
    if report.revenue is not None:
        console.print(
            f"[bold green]revenue[/] {report.revenue.amount_cents} "
            f"{report.revenue.currency}¢ ref={report.revenue.external_ref}"
        )


@app.command()
def revenue(
    opportunity_id: str | None = None,
    db: Path = Path("helis.db"),
) -> None:
    """Show persisted self-serve revenue events without network or model calls."""
    manager = _manager(db)
    events = manager.state.list_revenue(UUID(opportunity_id) if opportunity_id else None)
    table = Table("Amount", "Currency", "External ref", "Venture", "Recorded")
    totals: dict[str, int] = {}
    for event in events:
        totals[event.currency] = totals.get(event.currency, 0) + event.amount_cents
        table.add_row(
            str(event.amount_cents),
            event.currency,
            event.external_ref,
            str(event.opportunity_id),
            event.recorded_at.isoformat(timespec="seconds"),
        )
    console.print(table)
    if totals:
        console.print(
            "totals: " + ", ".join(f"{amount} {currency}¢" for currency, amount in totals.items())
        )


@app.command("gateway-status")
def gateway_status() -> None:
    gateway = ApprovedCommerceGateway.from_env()
    if gateway is None:
        console.print("commerce gateway: [yellow]not configured[/]")
        return
    console.print(f"commerce gateway: [green]{gateway.safe_destination}[/]")


if __name__ == "__main__":
    app()
