from __future__ import annotations

import json

from helis.budget import CycleBudget
from helis.builder_machine import BuilderMachine
from helis.builder_sandbox import BuildVerifier
from helis.domain import (
    BuildBundle,
    BuildFile,
    BuildSpec,
    BuildTemplate,
    Opportunity,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.store import HelisStore


class FakeProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        payload = self.payloads.pop(0)
        return ModelResult(content=json.dumps(payload), prompt_tokens=10, completion_tokens=5)


def _validated(engine: HelisEngine) -> Opportunity:
    opportunity = Opportunity(
        title="Quote workflow MVP",
        problem="Small service teams repeatedly lose time preparing manual quotes for customers.",
        customer="small service teams",
        proposed_value="make quote intake and response dramatically faster",
        stage=VentureStage.VALIDATED,
    )
    engine.ingest(opportunity)
    return opportunity


def test_builder_ignores_unvalidated_venture(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Unvalidated idea",
        problem="This idea has not yet passed real-world validation evidence.",
        customer="teams",
        proposed_value="unknown",
    )
    engine.ingest(opportunity)
    provider = FakeProvider([])
    report = BuilderMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=3),
        workspace_root=tmp_path / "workspaces",
    ).tick(opportunity.id)
    assert report.opportunity_id is None
    assert provider.calls == 0


def test_builder_creates_verified_preview_and_is_idempotent(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = _validated(engine)
    provider = FakeProvider(
        [
            {
                "template": "static_web_v1",
                "name": "FastQuote preview",
                "goal": "Show the validated faster-quoting value proposition clearly and honestly.",
                "acceptance_criteria": [
                    "State the recurring quoting problem",
                    "Explain the faster intake and response workflow",
                ],
            },
            {
                "files": [
                    {
                        "path": "index.html",
                        "content": (
                            "<!doctype html><html><body><main><h1>Faster quotes for service teams</h1>"
                            "<p>Capture the details once and prepare a clearer response faster.</p>"
                            "</main></body></html>"
                        ),
                    },
                    {
                        "path": "styles.css",
                        "content": "body { font-family: sans-serif; max-width: 60rem; margin: auto; }",
                    },
                    {
                        "path": "README.md",
                        "content": (
                            "# FastQuote preview\n\nA local-only MVP artifact for testing the validated "
                            "positioning. It makes no claims about customers, revenue or traction."
                        ),
                    },
                ]
            },
            {
                "verdict": "pass",
                "score": 8.5,
                "blocking_issues": [],
                "warnings": ["The value proposition still needs live customer testing."],
                "summary": "The preview matches the validated problem without fabricating traction.",
            },
        ]
    )
    machine = BuilderMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=3),
        workspace_root=tmp_path / "workspaces",
    )
    first = machine.tick(opportunity.id)
    assert first.preview is not None
    assert first.run is not None
    assert first.run.status.value == "ready_preview"
    assert (tmp_path / "workspaces" / str(opportunity.id) / str(first.run.id) / "index.html").exists()
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.READY_PREVIEW
    assert provider.calls == 3

    second = machine.tick(opportunity.id)
    assert second.preview is not None
    assert second.preview.id == first.preview.id
    assert provider.calls == 3


def test_verifier_rejects_path_escape_and_secret() -> None:
    spec = BuildSpec(
        opportunity_id=Opportunity(
            title="Safe build",
            problem="A sufficiently long problem statement for a constrained build.",
            customer="teams",
            proposed_value="safer previews",
        ).id,
        template=BuildTemplate.STATIC_WEB,
        name="Safe preview",
        goal="Create a safe local-only preview without credentials or path escapes.",
        acceptance_criteria=["Render a page", "Remain local"],
        max_files=4,
        max_total_bytes=80_000,
    )
    bundle = BuildBundle(
        files=[
            BuildFile(path="../escape.txt", content="secret"),
            BuildFile(
                path="index.html",
                content=(
                    "<html><body>api_key='12345678901234567890'"
                    "<h1>Unsafe</h1></body></html>"
                ),
            ),
            BuildFile(path="README.md", content="This artifact should fail deterministic checks."),
        ]
    )
    checks = BuildVerifier().verify(spec, bundle)
    by_name = {check.name: check.passed for check in checks}
    assert by_name["safe_paths"] is False
    assert by_name["allowed_paths"] is False
    assert by_name["secret_scan"] is False
