from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console

from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.store import HelisStore
from helis.validation_gateway import ApprovedValidationGateway
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
def advance(
    envelope_id: str,
    validation_cash_cents: float = typer.Option(0, min=0),
    max_tokens: int = typer.Option(70_000, min=1),
    max_model_cost_cents: float = typer.Option(20.0, min=0),
    workspace_root: Path = Path(".helis/workspaces"),
    db: Path = Path("helis.db"),
) -> None:
    """Run validate then build with one shared envelope-backed model budget."""
    report = _runtime(db, envelope_id, workspace_root).advance(
        max_tokens=max_tokens,
        max_model_cost_cents=max_model_cost_cents,
        validation_cash_cents=validation_cash_cents,
    )
    _print(report)


if __name__ == "__main__":
    app()
