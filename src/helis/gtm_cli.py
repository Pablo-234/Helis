from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from helis.budget import CycleBudget
from helis.contact_gateway import ApprovedContactGateway
from helis.engine import HelisEngine
from helis.gtm_discovery import GTMDiscoveryMachine
from helis.gtm_domain import LeadResponse
from helis.gtm_outreach import GTMContactPolicy, OutreachError, OutreachManager
from helis.gtm_store import GTMStore, lead_identity
from helis.model_provider import OpenAICompatibleProvider
from helis.prospect_gateway import ApprovedProspectGateway
from helis.store import HelisStore

app = typer.Typer(help="HELIS bounded go-to-market operations")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _manager(db: Path, *, with_gateway: bool = False) -> OutreachManager:
    gateway = ApprovedContactGateway.from_env() if with_gateway else None
    return OutreachManager(
        _engine(db),
        gateway=gateway,
        contact_policy=GTMContactPolicy(),
    )


@app.command()
def discover(
    db: Path = Path("helis.db"),
    opportunity_id: str | None = None,
    max_calls: int = 3,
    max_tokens: int = 40_000,
    max_cost_cents: float = 10.0,
) -> None:
    """Discover, evidence-bind, qualify and draft a tiny B2B prospect batch. Sends nothing."""
    gateway = ApprovedProspectGateway.from_env()
    if gateway is None:
        raise typer.BadParameter("HELIS_PROSPECT_GATEWAY_URL is not configured")
    helis = _engine(db)
    budget = CycleBudget(
        max_model_calls=max_calls,
        max_tokens=max_tokens,
        max_cost_cents=max_cost_cents,
    )
    report = GTMDiscoveryMachine(
        helis,
        OpenAICompatibleProvider.from_env(),
        budget,
        gateway,
    ).tick(UUID(opportunity_id) if opportunity_id else None)
    console.print(
        f"gtm: venture={report.opportunity_id or '-'} queries={report.queries_planned} "
        f"seen={report.candidates_seen} added={report.leads_added} "
        f"qualified={report.leads_qualified} drafts={report.drafts_created}"
    )
    console.print(
        f"usage: calls={budget.model_calls}/{budget.max_model_calls} "
        f"tokens={budget.tokens}/{budget.max_tokens} cost≈{budget.cost_cents:.3f}¢"
    )


@app.command()
def leads(opportunity_id: str, db: Path = Path("helis.db")) -> None:
    state = GTMStore(_engine(db).store)
    table = Table("Stage", "Fit", "Organization", "Channel", "Endpoint", "Lead")
    for lead in state.list_leads(UUID(opportunity_id)):
        table.add_row(
            lead.stage.value,
            f"{lead.fit_score:.1f}",
            lead.organization,
            lead.channel.value,
            lead.contact_endpoint or "-",
            str(lead.id),
        )
    console.print(table)


@app.command()
def drafts(opportunity_id: str, db: Path = Path("helis.db")) -> None:
    state = GTMStore(_engine(db).store)
    table = Table("Organization", "Channel", "Subject", "Draft")
    for draft in state.list_drafts(UUID(opportunity_id)):
        lead = state.get_lead(draft.lead_id)
        table.add_row(
            lead.organization if lead else "?",
            draft.channel.value,
            draft.subject or "-",
            str(draft.id),
        )
    console.print(table)


@app.command()
def prepare(draft_id: str, db: Path = Path("helis.db")) -> None:
    try:
        run = _manager(db).prepare(UUID(draft_id))
    except OutreachError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"outreach-run={run.id} status={run.status.value} hash={run.draft_hash[:12]}…"
    )


@app.command()
def approve(run_id: str, db: Path = Path("helis.db")) -> None:
    try:
        run = _manager(db).approve(UUID(run_id))
    except OutreachError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"approved outreach-run={run.id}; status={run.status.value}")


@app.command()
def dispatch(run_id: str, db: Path = Path("helis.db")) -> None:
    if ApprovedContactGateway.from_env() is None:
        raise typer.BadParameter("HELIS_CONTACT_GATEWAY_URL is not configured")
    try:
        run = _manager(db, with_gateway=True).dispatch(UUID(run_id))
    except OutreachError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"[bold green]dispatched[/] run={run.id} external_ref={run.external_ref or '-'}"
    )


@app.command("record-response")
def record_response(path: Path, db: Path = Path("helis.db")) -> None:
    response = LeadResponse.model_validate_json(path.read_text(encoding="utf-8"))
    try:
        stored, revenue = _manager(db).record_response(response)
    except OutreachError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"response={stored.kind.value} lead={stored.lead_id} revenue={stored.revenue_cents} "
        f"{stored.currency.upper()}"
    )
    if revenue is not None:
        console.print(
            f"[bold green]revenue attributed[/] {revenue.amount_cents} {revenue.currency} "
            f"venture={revenue.opportunity_id}"
        )


@app.command()
def revenue(opportunity_id: str | None = None, db: Path = Path("helis.db")) -> None:
    state = GTMStore(_engine(db).store)
    events = state.list_revenue(UUID(opportunity_id) if opportunity_id else None)
    table = Table("Amount", "Currency", "Lead", "Venture", "Recorded")
    totals: dict[str, int] = {}
    for event in events:
        totals[event.currency] = totals.get(event.currency, 0) + event.amount_cents
        table.add_row(
            str(event.amount_cents),
            event.currency,
            str(event.lead_id),
            str(event.opportunity_id),
            event.recorded_at.isoformat(timespec="seconds"),
        )
    console.print(table)
    if totals:
        console.print("totals: " + ", ".join(f"{value} {currency}" for currency, value in totals.items()))


@app.command()
def suppress(lead_id: str, reason: str, db: Path = Path("helis.db")) -> None:
    state = GTMStore(_engine(db).store)
    lead = state.get_lead(UUID(lead_id))
    if lead is None:
        raise typer.BadParameter("lead not found")
    state.suppress(lead_identity(lead), reason)
    console.print(f"suppressed {lead.organization}")


@app.command("gateway-status")
def gateway_status() -> None:
    prospect = ApprovedProspectGateway.from_env()
    contact = ApprovedContactGateway.from_env()
    console.print(
        "prospect gateway: "
        + (f"[green]{prospect.safe_destination}[/]" if prospect else "[yellow]not configured[/]")
    )
    console.print(
        "contact gateway: "
        + (f"[green]{contact.safe_destination}[/]" if contact else "[yellow]not configured[/]")
    )


if __name__ == "__main__":
    app()
