from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from helis.domain import (
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.preview_domain import PreviewPublishStatus
from helis.preview_gateway import PreviewGatewayAck
from helis.preview_publisher import PreviewPublisher
from helis.resource_envelope import EnvelopeExceeded, ResourceEnvelopeManager
from helis.store import HelisStore
from helis.venture_runtime import VentureRuntime


class FakeProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(content=json.dumps(self.payloads.pop(0)))


@dataclass(slots=True)
class FakePreviewGateway:
    safe_destination: str = "https://preview.example.test/publish"
    name: str = "fake_preview_gateway"
    calls: int = 0

    def execute(self, run, preview, bundle) -> PreviewGatewayAck:
        self.calls += 1
        return PreviewGatewayAck(
            accepted=True,
            dispatch_id=f"publish-{run.id}",
            preview_url="https://venture.example.test/",
        )


def _venture(engine: HelisEngine) -> Opportunity:
    opportunity = Opportunity(
        title="Envelope runtime venture",
        problem="Small service teams repeatedly lose time preparing manual quotes for customers.",
        customer="small service teams",
        proposed_value="make quote intake and response dramatically faster",
        stage=VentureStage.VALIDATED,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(capital_efficiency=8, execution_risk=3),
            total=76,
            recommendation=Recommendation.VALIDATE,
            rationale=["fixture"],
        )
    )
    return opportunity


def _envelope(engine: HelisEngine, *, cash: int = 1_000, calls: int = 3):
    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=cash,
            model_calls=calls,
            reserve_fraction=0,
            max_concentration=1,
        )
    )
    return ResourceEnvelopeManager(engine).activate(plan)[0]


def _builder_payloads() -> list[dict]:
    return [
        {
            "template": "static_web_v1",
            "name": "Envelope preview",
            "goal": "Show the validated quoting value proposition clearly.",
            "acceptance_criteria": ["State the problem", "Explain the faster workflow"],
        },
        {
            "files": [
                {
                    "path": "index.html",
                    "content": (
                        "<!doctype html><html><body><h1>Faster quotes</h1>"
                        "<p>Capture details once and prepare a clearer response faster.</p>"
                        "</body></html>"
                    ),
                },
                {
                    "path": "styles.css",
                    "content": "body { font-family: sans-serif; max-width: 60rem; margin: auto; }",
                },
                {
                    "path": "README.md",
                    "content": "# Preview\n\nLocal-only evidence-bound preview with no traction claims.",
                },
            ]
        },
        {
            "verdict": "pass",
            "score": 8.5,
            "blocking_issues": [],
            "warnings": [],
            "summary": "The artifact matches the validated claim without fabricated traction.",
        },
    ]


def test_builder_cannot_exceed_portfolio_model_call_envelope(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    envelope = _envelope(engine, calls=2)
    provider = FakeProvider(_builder_payloads())

    report = VentureRuntime(
        engine,
        provider,
        envelope.id,
        workspace_root=tmp_path / "workspaces",
    ).build()

    assert report.build is not None
    assert report.build.model_budget_exhausted is True
    assert provider.calls == 2
    current = ResourceEnvelopeManager(engine).get(envelope.id)
    assert current is not None and current.model_calls_consumed == 2


def test_builder_succeeds_when_envelope_has_three_calls(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    envelope = _envelope(engine, calls=3)
    provider = FakeProvider(_builder_payloads())

    report = VentureRuntime(
        engine,
        provider,
        envelope.id,
        workspace_root=tmp_path / "workspaces",
    ).build()

    assert report.build is not None and report.build.preview is not None
    assert provider.calls == 3
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.READY_PREVIEW
    current = ResourceEnvelopeManager(engine).get(envelope.id)
    assert current is not None and current.model_calls_consumed == 3


def test_advance_waits_for_publication_approval_then_launches_exact_preview(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _venture(engine)
    envelope = _envelope(engine, calls=4)
    provider = FakeProvider(_builder_payloads())
    gateway = FakePreviewGateway()
    runtime = VentureRuntime(
        engine,
        provider,
        envelope.id,
        workspace_root=tmp_path / "workspaces",
        preview_gateway=gateway,
    )
    built = runtime.build()
    assert built.build is not None and built.build.preview is not None
    assert provider.calls == 3

    waiting = runtime.advance()
    assert waiting.publication is not None
    assert waiting.publication.run is not None
    assert waiting.publication.run.status == PreviewPublishStatus.WAITING_APPROVAL
    assert waiting.publication.reason == "publication_waiting_approval"
    assert gateway.calls == 0
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.READY_PREVIEW

    PreviewPublisher(engine, workspace_root=tmp_path / "workspaces").approve(
        waiting.publication.run.id
    )
    launched = runtime.advance()

    assert launched.publication is not None
    assert launched.publication.publication is not None
    assert launched.publication.publication.preview_url == "https://venture.example.test/"
    assert gateway.calls == 1
    assert provider.calls == 3
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.LAUNCHED


def test_validation_cash_cap_cannot_exceed_remaining_envelope(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    envelope = _envelope(engine, cash=500, calls=1)
    runtime = VentureRuntime(engine, FakeProvider([]), envelope.id)

    with pytest.raises(EnvelopeExceeded):
        runtime.validate(validation_cash_cents=501)


def test_revoked_envelope_cannot_start_new_runtime(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine)
    first = _envelope(engine, cash=1_000, calls=2)
    _envelope(engine, cash=1_100, calls=3)

    with pytest.raises(EnvelopeExceeded):
        VentureRuntime(engine, FakeProvider([]), first.id)
