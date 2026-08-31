from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from helis.engine import HelisEngine
from helis.live_gateway_factory import live_gateways_from_env
from helis.preview_publisher import PreviewPublicationError, PreviewPublisher
from helis.store import HelisStore

app = typer.Typer(help="Prepare, approve and publish reviewed HELIS preview artifacts")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _publisher(
    db: Path,
    workspace_root: Path,
    *,
    with_gateway: bool = False,
) -> PreviewPublisher:
    gateway = live_gateways_from_env().preview if with_gateway else None
    return PreviewPublisher(
        _engine(db),
        workspace_root=workspace_root,
        gateway=gateway,
    )


@app.command()
def prepare(
    db: Path = Path("helis.db"),
    opportunity_id: str | None = None,
    workspace_root: Path = Path(".helis/workspaces"),
) -> None:
    """Create one idempotent publication run for a READY_PREVIEW artifact."""
    run = _publisher(db, workspace_root).prepare(
        UUID(opportunity_id) if opportunity_id else None
    )
    if run is None:
        console.print("preview publish: no READY_PREVIEW artifact found")
        return
    console.print(
        f"publish-run={run.id} status={run.status.value} "
        f"hash={run.artifact_hash[:12]}… approval={run.approval_granted}"
    )


@app.command()
def approve(
    run_id: str,
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
) -> None:
    """Approve exactly one prepared preview publication run."""
    try:
        run = _publisher(db, workspace_root).approve(UUID(run_id))
    except PreviewPublicationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"approved publish-run={run.id}; status={run.status.value}")


@app.command()
def publish(
    run_id: str,
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
) -> None:
    """Publish the exact reviewed hash through the selected live preview adapter."""
    gateway = live_gateways_from_env().preview
    if gateway is None:
        raise typer.BadParameter("no preview adapter is configured")
    publisher = PreviewPublisher(
        _engine(db),
        workspace_root=workspace_root,
        gateway=gateway,
    )
    try:
        publication = publisher.publish(UUID(run_id))
    except PreviewPublicationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"[bold green]published[/] hash={publication.artifact_hash[:12]}… "
        f"url={publication.preview_url or '-'}"
    )


@app.command()
def status(
    db: Path = Path("helis.db"),
    opportunity_id: str | None = None,
    workspace_root: Path = Path(".helis/workspaces"),
) -> None:
    """List publication runs without causing any side effects."""
    publisher = _publisher(db, workspace_root)
    runs = publisher.state.list_runs(
        UUID(opportunity_id) if opportunity_id else None
    )
    table = Table("Updated", "Status", "Approved", "Hash", "Run")
    for run in runs:
        table.add_row(
            run.updated_at.isoformat(timespec="seconds"),
            run.status.value,
            "yes" if run.approval_granted else "no",
            run.artifact_hash[:12],
            str(run.id),
        )
    console.print(table)


@app.command("gateway-status")
def gateway_status() -> None:
    gateway = live_gateways_from_env().preview
    if gateway is None:
        console.print("preview gateway: [yellow]not configured[/]")
        return
    console.print(
        f"preview gateway: [green]{getattr(gateway, 'name', type(gateway).__name__)}[/] "
        f"→ {gateway.safe_destination}"
    )


if __name__ == "__main__":
    app()
