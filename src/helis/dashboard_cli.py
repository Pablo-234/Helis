from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path

import typer
from rich.console import Console

from helis.dashboard import DashboardSnapshotBuilder
from helis.dashboard_web import dashboard_server

app = typer.Typer(help="Read-only local owner dashboard for HELIS")
console = Console()


@app.command()
def snapshot(
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
) -> None:
    """Print the same credential-free JSON snapshot used by the browser dashboard."""
    console.print_json(
        json.dumps(DashboardSnapshotBuilder(db, workspace_root).build(), ensure_ascii=False)
    )


@app.command()
def serve(
    db: Path = Path("helis.db"),
    workspace_root: Path = Path(".helis/workspaces"),
    port: int = typer.Option(8765, min=1024, max=65535),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Serve the read-only dashboard on localhost until Ctrl+C."""
    server = dashboard_server(db, workspace_root, port=port)
    url = f"http://127.0.0.1:{port}"
    console.print(f"HELIS owner dashboard: [link={url}]{url}[/link]")
    console.print("read-only localhost view; stop with Ctrl+C")
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\ndashboard stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    app()
