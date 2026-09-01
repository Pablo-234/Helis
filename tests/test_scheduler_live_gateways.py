from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import helis.portfolio_scheduler as scheduler_module
from helis.engine import HelisEngine
from helis.portfolio_scheduler import PortfolioScheduler
from helis.scheduler_cli import _control_loop
from helis.store import HelisStore


class NeverProvider:
    def complete(self, *, system: str, user: str):
        raise AssertionError("gateway wiring tests never call the model")


@dataclass(slots=True)
class StubGateway:
    name: str
    safe_destination: str


def test_default_runtime_receives_every_scheduler_gateway(monkeypatch, tmp_path: Path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    gateways = {
        "validation_gateway": StubGateway("validation", "https://validation.example.test"),
        "preview_gateway": StubGateway("preview", "https://preview.example.test"),
        "prospect_gateway": StubGateway("prospect", "https://prospect.example.test"),
        "contact_gateway": StubGateway("contact", "https://contact.example.test"),
        "contact_result_gateway": StubGateway("result", "https://result.example.test"),
        "commerce_gateway": StubGateway("commerce", "https://commerce.example.test"),
    }
    captured = {}

    class FakeRuntime:
        def __init__(self, *args, **kwargs) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(scheduler_module, "VentureRuntime", FakeRuntime)
    scheduler = PortfolioScheduler(
        engine,
        NeverProvider(),
        workspace_root=tmp_path / "workspaces",
        **gateways,
    )
    envelope_id = UUID("00000000-0000-0000-0000-000000000123")

    runtime = scheduler._default_runtime(envelope_id)

    assert isinstance(runtime, FakeRuntime)
    assert captured["args"] == (engine, scheduler.provider, envelope_id)
    assert captured["kwargs"] == {
        "workspace_root": tmp_path / "workspaces",
        **gateways,
    }


def test_recurring_control_loop_selects_all_direct_live_adapters(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HELIS_VERCEL_TOKEN", "vercel-secret")
    monkeypatch.setenv("HELIS_VERCEL_ORG_ID", "team_helis")
    monkeypatch.setenv("HELIS_VERCEL_PROJECT_ID", "prj_helis")
    monkeypatch.setenv("HELIS_BRAVE_SEARCH_API_KEY", "brave-secret")
    monkeypatch.setenv("HELIS_RESEND_API_KEY", "resend-secret")
    monkeypatch.setenv("HELIS_RESEND_FROM", "HELIS <hello@example.test>")
    monkeypatch.setenv("HELIS_RESEND_INBOUND_DOMAIN", "inbound.resend.app")
    monkeypatch.setenv("HELIS_STRIPE_SECRET_KEY", "stripe-secret")
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))

    scheduler = _control_loop(engine, tmp_path / "workspaces").scheduler

    assert scheduler.preview_gateway.name == "vercel_cli_preview_v1"
    assert scheduler.prospect_gateway.name == "brave_search_v1"
    assert scheduler.contact_gateway.name == "resend_email_v1"
    assert scheduler.contact_result_gateway.name == "resend_inbound_results_v1"
    assert scheduler.commerce_gateway.name == "stripe_payment_links_v1"
