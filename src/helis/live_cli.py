from __future__ import annotations

import os
import shutil

import typer
from rich.console import Console
from rich.table import Table

from helis.live_gateway_factory import live_gateways_from_env
from helis.model_provider import OpenAICompatibleProvider
from helis.validation_gateway import ApprovedValidationGateway
from helis.vercel_gateway import VercelCliPreviewGateway

app = typer.Typer(help="Inspect HELIS readiness for a real zero-to-revenue internet run")
console = Console()


def _yes(value: bool) -> str:
    return "[green]yes[/]" if value else "[yellow]no[/]"


@app.command()
def doctor() -> None:
    """Check local configuration without spending, publishing, emailing or calling external APIs."""
    live = live_gateways_from_env()
    model = OpenAICompatibleProvider.from_env()
    validation = ApprovedValidationGateway.from_env()

    rows: list[tuple[str, bool, str]] = []
    rows.append(("LLM", bool(model.base_url and model.model), f"{model.model} @ {model.base_url}"))
    for key, gateway in (
        ("Publication", live.preview),
        ("Prospecting", live.prospect),
        ("Outbound email", live.contact),
        ("Inbound replies", live.contact_result),
        ("Checkout/payment", live.commerce),
    ):
        rows.append(
            (
                key,
                gateway is not None,
                (
                    f"{getattr(gateway, 'name', type(gateway).__name__)} → {gateway.safe_destination}"
                    if gateway is not None
                    else "not configured"
                ),
            )
        )
    rows.append(
        (
            "External validation",
            validation is not None,
            validation.safe_destination if validation is not None else "optional; desk research still works",
        )
    )

    if isinstance(live.preview, VercelCliPreviewGateway):
        executable = shutil.which(live.preview.cli)
        rows.append(
            (
                "Vercel CLI",
                executable is not None,
                executable or f"'{live.preview.cli}' not found on PATH",
            )
        )

    table = Table("Capability", "Ready", "Selected adapter / detail")
    for capability, ready, detail in rows:
        table.add_row(capability, _yes(ready), detail)
    console.print(table)

    self_serve_ready = (
        bool(model.base_url and model.model)
        and live.preview is not None
        and live.commerce is not None
        and (
            not isinstance(live.preview, VercelCliPreviewGateway)
            or shutil.which(live.preview.cli) is not None
        )
    )
    b2b_ready = (
        bool(model.base_url and model.model)
        and live.preview is not None
        and live.prospect is not None
        and live.contact is not None
        and live.contact_result is not None
    )
    console.print(f"self-serve live path: {_yes(self_serve_ready)}")
    console.print(f"B2B live path: {_yes(b2b_ready)}")
    if self_serve_ready:
        console.print("[bold green]HELIS has the configured external hands for a self-serve live run.[/]")
    else:
        console.print("[yellow]Configure the missing publication/commerce prerequisites before live launch.[/]")


@app.command("env-example")
def env_example() -> None:
    """Print the direct-adapter environment variable names without exposing any secret values."""
    console.print(
        '''# Local/OpenAI-compatible model
$env:HELIS_LLM_BASE_URL="http://localhost:11434/v1"
$env:HELIS_LLM_MODEL="qwen3.5:9b"

# Vercel preview publication
$env:HELIS_VERCEL_TOKEN="<secret>"
$env:HELIS_VERCEL_ORG_ID="<org-id>"
$env:HELIS_VERCEL_PROJECT_ID="<project-id>"

# Brave Search prospecting
$env:HELIS_BRAVE_SEARCH_API_KEY="<secret>"
$env:HELIS_BRAVE_COUNTRY="PL"
$env:HELIS_BRAVE_SEARCH_LANG="pl"

# Resend outbound + inbound replies
$env:HELIS_RESEND_API_KEY="<secret>"
$env:HELIS_RESEND_FROM="HELIS <hello@your-domain.example>"
$env:HELIS_RESEND_INBOUND_DOMAIN="your-inbound.resend.app"

# Stripe self-serve checkout
$env:HELIS_STRIPE_SECRET_KEY="<secret>"'''
    )


@app.command("selected")
def selected() -> None:
    """Show adapter selection only; makes no network requests."""
    live = live_gateways_from_env()
    for key, name in live.names.items():
        console.print(f"{key}: {name or 'missing'}")
    if os.getenv("HELIS_PREVIEW_GATEWAY_URL") and live.names["preview"] != "approved_preview_gateway_v1":
        console.print("[dim]direct preview adapter takes precedence over generic preview gateway[/]")


if __name__ == "__main__":
    app()
