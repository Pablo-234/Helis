from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import uuid4

from helis.brave_gateway import BraveSearchProspectGateway
from helis.commerce_domain import BillingMode, CheckoutBinding, CheckoutRun, CommerceOffer
from helis.domain import (
    BuildBundle,
    BuildFile,
    DeliveryModel,
    PreviewManifest,
    RevenueModel,
)
from helis.gtm_domain import (
    Lead,
    LeadChannel,
    LeadStage,
    OutreachDraft,
    OutreachRun,
    OutreachRunStatus,
    ProspectEvidence,
    ProspectQuery,
)
from helis.preview_domain import PreviewPublishRun, PreviewPublishStatus
from helis.resend_gateway import ResendContactGateway, ResendContactResultGateway
from helis.stripe_gateway import StripeCommerceGateway
from helis.vercel_gateway import VercelCliPreviewGateway


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self.body


def test_brave_search_returns_only_observed_public_contact(monkeypatch) -> None:
    payload = {
        "web": {
            "results": [
                {
                    "title": "Example Automation Studio",
                    "url": "https://example.test/contact",
                    "description": "Custom automation projects. Email hello@example.test for details.",
                }
            ]
        }
    }
    monkeypatch.setattr(
        "helis.brave_gateway.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )
    gateway = BraveSearchProspectGateway(api_key="brave-secret")
    query = ProspectQuery(
        opportunity_id=uuid4(),
        query="automation agencies custom quoting",
        target_customer="automation agencies",
        must_have_signals=["custom quoting"],
    )

    candidates = gateway.search(query)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.website == "https://example.test/contact"
    assert candidate.channel == LeadChannel.EMAIL
    assert candidate.contact_endpoint == "hello@example.test"
    assert candidate.evidence[0].source == "brave_search_api"


def _commerce_offer() -> CommerceOffer:
    return CommerceOffer(
        opportunity_id=uuid4(),
        offer_hash="a" * 64,
        name="Tiny workflow product",
        description="A small paid workflow product for service teams.",
        price_cents=2500,
        currency="PLN",
        pricing_unit="per month",
        billing_mode=BillingMode.SUBSCRIPTION,
        revenue_model=RevenueModel.SUBSCRIPTION,
        delivery_model=DeliveryModel.SOFTWARE,
    )


def test_stripe_creates_exact_payment_link_and_observes_paid_session(monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/v1/payment_links"):
            form = parse_qs(request.data.decode("utf-8"))
            assert form["line_items[0][price_data][currency]"] == ["pln"]
            assert form["line_items[0][price_data][unit_amount]"] == ["2500"]
            assert form["line_items[0][price_data][recurring][interval]"] == ["month"]
            return FakeResponse(
                {
                    "id": "plink_helis",
                    "url": "https://buy.stripe.com/test_helis",
                    "active": True,
                    "livemode": False,
                }
            )
        assert "payment_link=plink_helis" in request.full_url
        return FakeResponse(
            {
                "data": [
                    {
                        "id": "cs_paid_helis",
                        "payment_link": "plink_helis",
                        "payment_status": "paid",
                        "amount_total": 2500,
                        "currency": "pln",
                        "mode": "subscription",
                        "livemode": False,
                    }
                ]
            }
        )

    monkeypatch.setattr("helis.stripe_gateway.urlopen", fake_urlopen)
    gateway = StripeCommerceGateway(secret_key="sk_test_helis")
    offer = _commerce_offer()
    run = CheckoutRun(
        offer_id=offer.id,
        opportunity_id=offer.opportunity_id,
        offer_hash=offer.offer_hash,
        approval_granted=True,
    )

    ack = gateway.create_checkout(run, offer)
    result = gateway.poll_payment(
        CheckoutBinding(
            run_id=run.id,
            offer_id=offer.id,
            opportunity_id=offer.opportunity_id,
            offer_hash=offer.offer_hash,
            checkout_url=ack.checkout_url,
            external_ref=ack.external_ref,
        )
    )

    assert ack.external_ref == "plink_helis"
    assert result is not None
    assert result.status.value == "paid"
    assert result.external_ref == "cs_paid_helis"
    assert result.amount_cents == 2500
    assert result.currency == "PLN"
    assert len(requests) == 2


def _outreach_objects():
    opportunity_id = uuid4()
    evidence = ProspectEvidence(
        source="public page",
        reason="The public page contains a direct business email address.",
        source_url="https://example.test/contact",
    )
    lead = Lead(
        opportunity_id=opportunity_id,
        organization="Example Company",
        website="https://example.test",
        contact_endpoint="owner@example.test",
        channel=LeadChannel.EMAIL,
        evidence=[evidence],
        fit_score=8,
        stage=LeadStage.DRAFTED,
    )
    draft = OutreachDraft(
        lead_id=lead.id,
        opportunity_id=opportunity_id,
        channel=LeadChannel.EMAIL,
        contact_endpoint="owner@example.test",
        subject="Question about your workflow",
        body="I noticed your public workflow and wanted to ask one short, relevant question.",
        evidence_ids=[evidence.id],
    )
    run = OutreachRun(
        draft_id=draft.id,
        lead_id=lead.id,
        opportunity_id=opportunity_id,
        draft_hash="b" * 64,
        status=OutreachRunStatus.READY,
        approval_granted=True,
        dispatched_at=datetime.now(UTC),
    )
    return lead, draft, run


def test_resend_sends_with_per_run_reply_address(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse({"id": "resend-email-id"})

    monkeypatch.setattr("helis.resend_gateway.urlopen", fake_urlopen)
    gateway = ResendContactGateway(
        api_key="re_secret",
        from_email="HELIS <hello@sender.test>",
        inbound_domain="inbound.resend.app",
    )
    lead, draft, run = _outreach_objects()

    ack = gateway.send(run, lead, draft)

    assert ack.dispatch_id == "resend-email-id"
    assert captured["to"] == ["owner@example.test"]
    assert captured["reply_to"] == f"helis-{run.id}@inbound.resend.app"
    assert captured["text"] == draft.body


def test_resend_reads_observed_reply_without_inventing_revenue(monkeypatch) -> None:
    lead, draft, run = _outreach_objects()
    target = f"helis-{run.id}@inbound.resend.app"

    def fake_urlopen(request, timeout):
        if "/emails/receiving?" in request.full_url:
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "received-1",
                            "to": [target],
                            "created_at": datetime.now(UTC).isoformat(),
                        }
                    ]
                }
            )
        return FakeResponse(
            {
                "id": "received-1",
                "to": [target],
                "from": "owner@example.test",
                "subject": "Re: Question about your workflow",
                "text": "Yes, this sounds useful. Let's talk next week.",
            }
        )

    monkeypatch.setattr("helis.resend_gateway.urlopen", fake_urlopen)
    gateway = ResendContactResultGateway(
        api_key="re_secret",
        inbound_domain="inbound.resend.app",
    )

    response = gateway.fetch(run)

    assert response is not None
    assert response.kind.value == "meeting"
    assert response.revenue_cents == 0
    assert response.run_id == run.id
    assert response.lead_id == lead.id
    assert draft.opportunity_id == response.opportunity_id


def test_vercel_gateway_stages_exact_bundle_and_runs_fixed_preview_command(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("helis.vercel_gateway.shutil.which", lambda executable: "/usr/bin/vercel")

    def fake_run(command, *, cwd, env, capture_output, text, timeout, check):
        captured["command"] = command
        root = Path(cwd)
        assert (root / "index.html").read_text(encoding="utf-8") == "<html><body>HELIS</body></html>"
        project = json.loads((root / ".vercel" / "project.json").read_text(encoding="utf-8"))
        assert project == {"orgId": "team_helis", "projectId": "prj_helis"}
        assert env["VERCEL_ORG_ID"] == "team_helis"
        assert env["VERCEL_PROJECT_ID"] == "prj_helis"
        return SimpleNamespace(
            returncode=0,
            stdout="Preview: https://helis-preview-123.vercel.app\n",
            stderr="",
        )

    monkeypatch.setattr("helis.vercel_gateway.subprocess.run", fake_run)
    opportunity_id = uuid4()
    preview = PreviewManifest(
        run_id=uuid4(),
        opportunity_id=opportunity_id,
        workspace="unused",
        entrypoint="index.html",
        artifact_hash="c" * 64,
    )
    run = PreviewPublishRun(
        preview_id=preview.id,
        opportunity_id=opportunity_id,
        artifact_hash=preview.artifact_hash,
        status=PreviewPublishStatus.READY,
        approval_granted=True,
    )
    bundle = BuildBundle(files=[BuildFile(path="index.html", content="<html><body>HELIS</body></html>")])
    gateway = VercelCliPreviewGateway(
        token="vercel-secret",
        org_id="team_helis",
        project_id="prj_helis",
    )

    ack = gateway.execute(run, preview, bundle)

    assert ack.preview_url == "https://helis-preview-123.vercel.app"
    command = captured["command"]
    assert isinstance(command, list)
    assert command[:4] == ["/usr/bin/vercel", "deploy", "--yes", "--target=preview"]
    assert "--token" in command
