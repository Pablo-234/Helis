from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console

from helis.contact_gateway import ApprovedContactGateway
from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.prospect_gateway import ApprovedProspectGateway
from helis.store import HelisStore
from helis.validation_gateway import ApprovedValidationGateway
from helis.venture_architecture_store import VentureArchitectureStore
from helis.venture_runtime import VentureRuntime, VentureRuntimeReport

app = typer.Typer(help="Run one HELIS venture inside its active portfolio resource envelope")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _runtime(
    db: Path,
    envelope_id: str,
    workspace_root: Path,
) -> VentureRuntime:
    return VentureRuntime(
        _engine(db),
        OpenAICompatibleProvider.from_env(),
        UUID(envelope_id),
        workspace_root=workspace_root,
        validation_gateway=ApprovedValidationGateway.from_env(),
        prospect_gateway=ApprovedProspectGateway.from_env(),
        contact_gateway=ApprovedContactGateway.from_env(),
    )


def _print(report: VentureRuntimeReport) -> None:
    envelope = report.envelope
    console.print(
        f"envelope={envelope.id} venture={envelope.opportunity_id} status={envelope.status.value}"
    )
    console.print(
        f"usage: calls={report.budget.model_calls}/{report.budget.max_model_calls} "
        f"tokens={report.budget.tokens}/{report.budget.max_tokens} "
        f"configured-model-cost≈{report.budget.cost_cents:.3f}¢"
    )
    console.print(
        f"remaining envelope: cash={envelope.remaining_cash_cents}/{envelope.cash_limit_cents} "
        f"{envelope.currency}¢ calls={envelope.remaining_model_calls}/{envelope.model_call_limit}"
    )
    if report.validation is not None:
        item = report.validation
        console.print(
            f"validation: venture={item.opportunity_id or '-'} "
            f"waiting_approval={item.waiting_approval} waiting_result={item.waiting_result} "
            f"blocked={item.blocked} model_budget_exhausted={item.model_budget_exhausted}"
        )
        if item.decision is not None:
            console.print(
                f"validation decision={item.decision.decision.value} "
                f"confidence={item.decision.confidence:.2f}"
            )
    if report.architecture is not None:
        item = report.architecture
        architecture = item.architecture
        console.print(
            f"architecture: created={item.created} blocked={item.blocked_reason or '-'} "
            f"model_budget_exhausted={item.model_budget_exhausted}"
        )
        if architecture is not None:
            ai_agents = sum(
                capability.implementation.value == "ai_agent"
                for capability in architecture.capabilities
            )
            console.print(
                f"architecture id={architecture.id} capabilities={len(architecture.capabilities)} "
                f"ai_agents={ai_agents} snapshot={architecture.input_hash[:12]}…"
            )
    if report.build is not None:
        item = report.build
        console.print(
            f"build: venture={item.opportunity_id or '-'} "
            f"model_budget_exhausted={item.model_budget_exhausted} "
            f"blocked={item.blocked_reason or '-'}"
        )
        if item.preview is not None:
            console.print(
                f"[green]preview ready[/] {item.preview.entrypoint} "
                f"hash={item.preview.artifact_hash[:12]}…"
            )
    if report.gtm is not None:
        item = report.gtm
        console.print(
            f"gtm: reason={item.reason} waiting_approval={item.waiting_approval} "
            f"waiting_result={item.waiting_result} prepared={item.prepared_run_id or '-'} "
            f"dispatched={item.dispatched_run_id or '-'}"
        )
        if item.discovery is not None:
            discovery = item.discovery
            console.print(
                f"discovery: queries={discovery.queries_planned} candidates={discovery.candidates_seen} "
                f"leads={discovery.leads_added} qualified={discovery.leads_qualified} "
                f"drafts={discovery.drafts_created}"
            )


@app.command()
def validate(
    envelope_id: str,
    validation_cash_cents: float = typer.Option(0, min=0),
    max_tokens: int = typer.Option(35_000, min=1),
    max_model_cost_cents: float = typer.Option(10.0, min=0),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Execute one venture validation tick under its active resource envelope."""
    report = _runtime(db, envelope_id, workspace_root).validate(
        max_tokens=max_tokens,
        max_model_cost_cents=max_model_cost_cents,
        validation_cash_cents=validation_cash_cents,
    )
    _print(report)


@app.command()
def build(
    envelope_id: str,
    max_tokens: int = typer.Option(45_000, min=1),
    max_model_cost_cents: float = typer.Option(15.0, min=0),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Build one validated venture under its active resource envelope."""
    report = _runtime(db, envelope_id, workspace_root).build(
        max_tokens=max_tokens,
        max_model_cost_cents=max_model_cost_cents,
    )
    _print(report)


@app.command()
def market(
    envelope_id: str,
    max_tokens: int = typer.Option(45_000, min=1),
    max_model_cost_cents: float = typer.Option(15.0, min=0),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Advance one GTM tick without ever granting external-contact approval."""
    report = _runtime(db, envelope_id, workspace_root).market(
        max_tokens=max_tokens,
        max_model_cost_cents=max_model_cost_cents,
    )
    _print(report)


@app.command()
def architecture(
    opportunity_id: str,
    db: Path = Path("helis.db"),
) -> None:
    """Show the latest persisted child-venture capability graph without model/network calls."""
    engine = _engine(db)
    item = VentureArchitectureStore(engine.store).latest(UUID(opportunity_id))
    if item is None:
        console.print("no architecture")
        raise typer.Exit(code=1)
    console.print(
        f"architecture={item.id} venture={item.opportunity_id} snapshot={item.input_hash}"
    )
    for capability in item.capabilities:
        actions = ",".join(action.value for action in capability.required_actions) or "none"
        dependencies = ",".join(capability.depends_on) or "none"
        console.print(
            f"- {capability.key}: {capability.implementation.value} "
            f"depends_on={dependencies} actions={actions} metric={capability.success_metric}"
        )
    if item.owner_responsibilities:
        console.print("owner responsibilities:")
        for responsibility in item.owner_responsibilities:
            console.print(f"  - {responsibility}")


@app.command()
def advance(
    envelope_id: str,
    validation_cash_cents: float = typer.Option(0, min=0),
    max_tokens: int = typer.Option(70_000, min=1),
    max_model_cost_cents: float = typer.Option(20.0, min=0),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Advance the venture's current validation/architecture/build/GTM lifecycle phase."""
    report = _runtime(db, envelope_id, workspace_root).advance(
        max_tokens=max_tokens,
        max_model_cost_cents=max_model_cost_cents,
        validation_cash_cents=validation_cash_cents,
    )
    _print(report)


if __name__ == "__main__":
    app()
