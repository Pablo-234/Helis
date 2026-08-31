from __future__ import annotations

from helis.live_gateway_factory import live_gateways_from_env


def test_direct_live_adapters_are_selected_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("HELIS_VERCEL_TOKEN", "vercel-secret")
    monkeypatch.setenv("HELIS_VERCEL_ORG_ID", "team_helis")
    monkeypatch.setenv("HELIS_VERCEL_PROJECT_ID", "prj_helis")
    monkeypatch.setenv("HELIS_BRAVE_SEARCH_API_KEY", "brave-secret")
    monkeypatch.setenv("HELIS_RESEND_API_KEY", "resend-secret")
    monkeypatch.setenv("HELIS_RESEND_FROM", "HELIS <hello@example.test>")
    monkeypatch.setenv("HELIS_RESEND_INBOUND_DOMAIN", "inbound.resend.app")
    monkeypatch.setenv("HELIS_STRIPE_SECRET_KEY", "stripe-secret")

    selected = live_gateways_from_env()

    assert selected.names == {
        "preview": "vercel_cli_preview_v1",
        "prospect": "brave_search_v1",
        "contact": "resend_email_v1",
        "contact_result": "resend_inbound_results_v1",
        "commerce": "stripe_payment_links_v1",
    }
