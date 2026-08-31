from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from helis.budget import CycleBudget
from helis.engine import HelisEngine
from helis.gtm_channel_experiment import (
    GTMChannelExperimentManager,
    GTMChannelExperimentStore,
)
from helis.gtm_discovery import GTMDiscoveryMachine
from helis.gtm_domain import LeadResponse, lead_contact_options
from helis.gtm_experiment import GTMExperimentManager
from helis.gtm_experiment_store import GTMExperimentStore
from helis.gtm_outreach import GTMContactPolicy, OutreachError, OutreachManager
from helis.gtm_store import GTMStore, lead_identity
from helis.live_gateway_factory import live_gateways_from_env
from helis.model_provider import OpenAICompatibleProvider
from helis.store import HelisStore

app = typer.Typer(help="HELIS bounded go-to-market operations")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _manager(db: Path, *, with_gateway: bool = False) -> OutreachManager:
    gateway = live_gateways_from_env().contact if with_gateway else None
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
    gateway = live_gateways_from_env().prospect
    if gateway is None:
        raise typer.BadParameter("no prospect adapter is configured")
    helis = _engine(db)
    provider = OpenAICompatibleProvider.from_env()
    budget = CycleBudget(
        max_model_calls=max_calls,
        max_tokens=max_tokens,
        max_cost_cents=max_cost_cents,
    )
    experiments = GTMExperimentManager(helis, provider, budget)
    channels = GTMChannelExperimentManager(helis)
    report = GTMDiscoveryMachine(
        helis,
        provider,
        budget,
        gateway,
        experiment_manager=experiments,
        channel_experiment_manager=channels,
    ).tick(UUID(opportunity_id) if opportunity_id else None)
    console.print(
        f"gtm: venture={report.opportunity_id or '-'} queries={report.queries_planned} "
        f"seen={report.candidates_seen} added={report.leads_added} "
        f"qualified={report.leads_qualified} drafts={report.drafts_created}"
    )
    console.print(
        f"experiments: offer={report.experiment_id or '-'} "
        f"offer_assignments={report.experiment_assignments} "
        f"channel={report.channel_experiment_id or '-'} "
        f"channel_assignments={report.channel_experiment_assignments}"
    )
    console.print(
        f"usage: calls={budget.model_calls}/{budget.max_model_calls} "
        f"tokens={budget.tokens}/{budget.max_tokens} cost≈{budget.cost_cents:.3f}¢"
    )


@app.command()
def leads(opportunity_id: str, db: Path = Path("helis.db")) -> None:
    state = GTMStore(_engine(db).store)
    table = Table("Stage", "Fit", "Organization", "Channels", "Primary endpoint", "Lead")
    for lead in state.list_leads(UUID(opportunity_id)):
        channels = ",".join(sorted({item.channel.value for item in lead_contact_options(lead)})) or "-"
        table.add_row(
            lead.stage.value,
            f"{lead.fit_score:.1f}",
            lead.organization,
            channels,
            lead.contact_endpoint or "-",
            str(lead.id),
        )
    console.print(table)


@app.command()
def drafts(opportunity_id: str, db: Path = Path("helis.db")) -> None:
    state = GTMStore(_engine(db).store)
    table = Table(
        "Organization",
        "Channel",
        "Endpoint",
        "Subject",
        "Offer arm",
        "Channel arm",
        "Draft",
    )
    for draft in state.list_drafts(UUID(opportunity_id)):
        lead = state.get_lead(draft.lead_id)
        table.add_row(
            lead.organization if lead else "?",
            draft.channel.value,
            draft.contact_endpoint or "-",
            draft.subject or "-",
            draft.experiment_arm_key or "-",
            draft.channel_experiment_arm_key or "-",
            str(draft.id),
        )
    console.print(table)


@app.command()
def experiments(opportunity_id: str, db: Path = Path("helis.db")) -> None:
    """Show persisted bounded offer/pricing experiments without model/network calls."""
    engine = _engine(db)
    state = GTMExperimentStore(engine.store)
    items = state.list(UUID(opportunity_id))
    table = Table("Status", "Kind", "Arm", "Price", "Winner", "Experiment")
    for experiment in items:
        for arm in experiment.arms:
            price = (
                f"{arm.price_cents} {arm.currency.upper()}"
                if arm.price_cents is not None
                else "-"
            )
            table.add_row(
                experiment.status.value,
                experiment.kind.value,
                f"{arm.key}: {arm.label}",
                price,
                experiment.winner_arm_key or "-",
                str(experiment.id),
            )
    console.print(table)
    if not items:
        console.print("[yellow]no GTM experiments for this venture[/]")


@app.command("channel-experiments")
def channel_experiments(opportunity_id: str, db: Path = Path("helis.db")) -> None:
    """Show persisted bounded acquisition-channel experiments."""
    engine = _engine(db)
    state = GTMChannelExperimentStore(engine)
    items = state.list(UUID(opportunity_id))
    table = Table("Status", "Arm", "Channel", "Winner", "Conclusion", "Experiment")
    for experiment in items:
        for arm in experiment.arms:
            table.add_row(
                experiment.status.value,
                arm.key,
                arm.channel.value,
                experiment.winner_arm_key or "-",
                experiment.conclusion or "-",
                str(experiment.id),
            )
    console.print(table)
    if not items:
        console.print("[yellow]no channel experiments for this venture[/]")


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
    if live_gateways_from_env().contact is None:
        raise typer.BadParameter("no contact adapter is configured")
    try:
        run = _manager(db, with_gateway=True).dispatch(UUID(run_id))
    except OutreachError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"[bold green]dispatched[/] run={run.id} external_ref={run.external_ref or '-'}"
    )


@app.command("poll-result")
def poll_result(run_id: str, db: Path = Path("helis.db")) -> None:
    """Read an observed reply for one dispatched run and persist it without inventing revenue."""
    live = live_gateways_from_env()
    if live.contact_result is None:
        raise typer.BadParameter("no contact-result adapter is configured")
    engine = _engine(db)
    state = GTMStore(engine.store)
    run = state.get_outreach_run(UUID(run_id))
    if run is None:
        raise typer.BadParameter("outreach run not found")
    response = live.contact_result.fetch(run)
    if response is None:
        console.print("result: [yellow]pending[/]")
        return
    try:
        stored, revenue_event = OutreachManager(engine).record_response(response)
    except OutreachError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"result={stored.kind.value} summary={stored.summary[:160]}")
    if revenue_event is not None:
        console.print(
            f"[bold green]revenue[/] {revenue_event.amount_cents} {revenue_event.currency}¢"
        )


@app.command("record-response")
def record_response(path: Path, db: Path = Path("helis.db")) -> None:
    response = LeadResponse.model_validate_json(path.read_text(encoding="utf-8"))
    try:
        stored, revenue_event = _manager(db).record_response(response)
    except OutreachError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"response={stored.kind.value} lead={stored.lead_id} revenue={stored.revenue_cents} "
        f"{stored.currency.upper()}"
    )
    if revenue_event is not None:
        console.print(
            f"[bold green]revenue attributed[/] {revenue_event.amount_cents} "
            f"{revenue_event.currency} venture={revenue_event.opportunity_id}"
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
    live = live_gateways_from_env()
    for key, gateway in (
        ("prospect", live.prospect),
        ("contact", live.contact),
        ("contact-result", live.contact_result),
    ):
        if gateway is None:
            console.print(f"{key}: [yellow]not configured[/]")
        else:
            console.print(
                f"{key}: [green]{getattr(gateway, 'name', type(gateway).__name__)}[/] "
                f"→ {gateway.safe_destination}"
            )


if __name__ == "__main__":
    app()
