from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from helis.engine import HelisEngine
from helis.operator_domain import OperatorRequestType
from helis.operator_inbox import OperatorInbox, OperatorInboxError
from helis.store import HelisStore

app = typer.Typer(help="Inspect and decide every pending HELIS operator request")
console = Console()


def _inbox(
    db: Path,
    workspace_root: Path,
    self_improvement_root: Path,
) -> OperatorInbox:
    return OperatorInbox(
        HelisEngine(HelisStore(db)),
        workspace_root=workspace_root,
        self_improvement_root=self_improvement_root,
    )


@app.command("inbox")
def list_inbox(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    self_improvement_root: Path = Path(".helis/self-improvement"),
) -> None:
    """List all unresolved approvals and non-AI capability inputs without side effects."""
    items = _inbox(
        db,
        workspace_root,
        self_improvement_root,
    ).list_items()
    if json_output:
        console.print_json(json.dumps([item.model_dump(mode="json") for item in items]))
        return
    table = Table("Priority", "Type", "Kind", "Venture", "Title", "Token", "Key")
    for item in items:
        table.add_row(
            str(item.priority),
            item.request_type.value,
            item.kind.value,
            Text(item.venture_title),
            Text(item.title),
            item.confirmation_token or "input",
            Text(item.key),
        )
    console.print(table)
    console.print(f"pending={len(items)}")


@app.command()
def show(
    key: str,
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    self_improvement_root: Path = Path(".helis/self-improvement"),
) -> None:
    """Show the exact consequence, immutable binding and next command for one request."""
    item = _inbox(
        db,
        workspace_root,
        self_improvement_root,
    ).get(key)
    if item is None:
        raise typer.BadParameter("operator request is missing, stale or already resolved")
    console.print(item.title, style="bold", markup=False)
    console.print(f"key: {item.key}", markup=False)
    console.print(
        f"type: {item.request_type.value}; kind: {item.kind.value}",
        markup=False,
    )
    console.print(
        f"venture: {item.venture_title} ({item.opportunity_id or '-'})",
        markup=False,
    )
    console.print(f"summary: {item.summary}", markup=False)
    console.print(f"consequence: {item.consequence}", markup=False)
    console.print(f"binding: {item.binding}", markup=False)
    for name, value in item.details.items():
        console.print(f"{name}: {value}", markup=False)
    console.print(f"next: {item.action_command}", markup=False)
    if item.request_type == OperatorRequestType.APPROVAL:
        console.print(
            f"reject: helis-operator reject {item.key} --confirm "
            f"{item.confirmation_token} --reason \"<reason>\"",
            markup=False,
        )


@app.command()
def approve(
    key: str,
    confirm: str = typer.Option(..., help="Current 16-character request snapshot token"),
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    self_improvement_root: Path = Path(".helis/self-improvement"),
) -> None:
    """Approve exactly one current hash-confirmed request; does not execute its side effect."""
    try:
        receipt = _inbox(
            db,
            workspace_root,
            self_improvement_root,
        ).approve(key, confirmation_token=confirm)
    except (OperatorInboxError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"approved key={receipt.key} status={receipt.resulting_status} "
        f"token={receipt.confirmation_token}",
        style="green",
        markup=False,
    )
    console.print("approval recorded; the scheduler still performs the separately gated action")


@app.command()
def reject(
    key: str,
    confirm: str = typer.Option(..., help="Current 16-character request snapshot token"),
    reason: str = typer.Option(..., help="Audited rejection reason"),
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    self_improvement_root: Path = Path(".helis/self-improvement"),
) -> None:
    """Cancel exactly one current hash-confirmed approval request and audit the reason."""
    try:
        receipt = _inbox(
            db,
            workspace_root,
            self_improvement_root,
        ).reject(key, confirmation_token=confirm, reason=reason)
    except (OperatorInboxError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"rejected key={receipt.key} status={receipt.resulting_status} "
        f"reason={receipt.reason}",
        style="yellow",
        markup=False,
    )


if __name__ == "__main__":
    app()
