from __future__ import annotations

import json

from helis.budget import CycleBudget
from helis.build_domain import BuildBundle, BuildFile, BuildRuntime, BuildRunStatus, BuildSpec
from helis.build_generator import BuildBundleViolation, BundleLimits, validate_bundle
from helis.builder import BuilderMachine
from helis.domain import Opportunity, VentureStage
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.sandbox import DockerPythonSandbox, SandboxStatus
from helis.store import HelisStore


class FakeProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)

    def complete(self, *, system: str, user: str) -> ModelResult:
        payload = self.payloads.pop(0)
        return ModelResult(content=json.dumps(payload), prompt_tokens=10, completion_tokens=10)


def test_bundle_rejects_workspace_escape() -> None:
    spec = BuildSpec(
        opportunity_id=Opportunity(
            title="x venture",
            problem="A sufficiently detailed problem statement.",
            customer="teams",
            proposed_value="solve it",
        ).id,
        product_name="Tiny MVP",
        objective="Provide a tiny offline interface for the validated workflow.",
        target_user="teams",
        core_flows=["open the tool"],
        acceptance_criteria=["index renders locally"],
        runtime=BuildRuntime.STATIC_WEB,
    )
    bundle = BuildBundle(
        spec_id=spec.id,
        files=[BuildFile(path="../escape.html", content="nope")],
    )
    try:
        validate_bundle(bundle, spec, BundleLimits())
    except BuildBundleViolation:
        pass
    else:
        raise AssertionError("workspace escape was not rejected")


def test_static_builder_creates_isolated_tested_workspace(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Validated workflow tool",
        problem="Operators repeatedly lose time performing a validated manual workflow.",
        customer="operators",
        proposed_value="provide a tiny local workflow helper",
        stage=VentureStage.VALIDATED,
    )
    engine.ingest(opportunity)
    provider = FakeProvider(
        [
            {
                "opportunity_id": str(opportunity.id),
                "product_name": "Workflow Helper",
                "objective": "Provide the smallest local interface for the validated workflow.",
                "target_user": "operators",
                "core_flows": ["open helper", "enter workflow data", "see result"],
                "acceptance_criteria": ["works offline", "main flow is visible"],
                "non_goals": ["payments", "accounts", "deployment"],
                "runtime": "static_web",
            },
            {
                "spec_id": "SPEC_ID_PLACEHOLDER",
                "files": [
                    {
                        "path": "index.html",
                        "role": "entrypoint",
                        "content": "<!doctype html><html><body><main id='app'>Workflow Helper</main><script src='app.js'></script></body></html>",
                    },
                    {
                        "path": "app.js",
                        "role": "source",
                        "content": "document.querySelector('#app').dataset.ready = 'true';",
                    },
                ],
            },
        ]
    )

    # The generator must echo the planner-created spec id, so patch the fake response between calls.
    machine = BuilderMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=3),
        workspace_root=tmp_path / "workspaces",
    )
    spec = machine.planner.plan(opportunity, [])
    machine.build_store.save_spec(spec)
    engine.store.save_opportunity(opportunity.model_copy(update={"stage": VentureStage.BUILDING}))
    provider.payloads[0]["spec_id"] = str(spec.id)

    report = machine.tick(opportunity.id)
    assert report.run is not None
    assert report.run.status == BuildRunStatus.TESTED
    assert report.run.workspace_path is not None
    workspace = tmp_path / "workspaces" / str(opportunity.id) / str(report.run.id)
    assert workspace.exists()
    assert (workspace / "index.html").is_file()
    assert (workspace / "helis-build-manifest.json").is_file()
    assert not (tmp_path / "escape.html").exists()
    assert engine.store.get_opportunity(opportunity.id).stage == VentureStage.BUILDING


def test_python_sandbox_never_falls_back_to_host(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("helis.sandbox.shutil.which", lambda _: None)
    report = DockerPythonSandbox().verify(tmp_path)
    assert report.status == SandboxStatus.BLOCKED
    assert "host execution is forbidden" in report.stderr
