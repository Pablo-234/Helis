from __future__ import annotations

from dataclasses import dataclass

import pytest

from helis.builder_sandbox import BuildSandbox, bundle_hash
from helis.domain import (
    BuildBundle,
    BuildFile,
    BuildReview,
    BuildReviewVerdict,
    BuildRun,
    BuildSpec,
    BuildStatus,
    BuildTemplate,
    Opportunity,
    PreviewManifest,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.preview_domain import PreviewPublishStatus
from helis.preview_gateway import PreviewGatewayAck
from helis.preview_publisher import PreviewPublicationError, PreviewPublisher
from helis.store import HelisStore


@dataclass(slots=True)
class FakeGateway:
    safe_destination: str = "https://preview.example.test/publish"
    name: str = "fake_preview_gateway"
    calls: int = 0

    def execute(self, run, preview, bundle) -> PreviewGatewayAck:
        self.calls += 1
        assert bundle_hash(bundle) == preview.artifact_hash == run.artifact_hash
        return PreviewGatewayAck(
            accepted=True,
            dispatch_id=f"publish-{run.id}",
            preview_url="https://demo.example.test/venture",
            metadata={"provider": "fake"},
        )


def _ready_preview(tmp_path) -> tuple[HelisEngine, Opportunity, PreviewManifest, BuildRun]:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Reviewed preview",
        problem="Small service teams repeatedly lose time preparing manual quotes for customers.",
        customer="small service teams",
        proposed_value="prepare clearer quotes faster",
        stage=VentureStage.VALIDATED,
    )
    engine.ingest(opportunity)
    spec = BuildSpec(
        opportunity_id=opportunity.id,
        template=BuildTemplate.STATIC_WEB,
        name="Reviewed quote preview",
        goal="Present the validated quote workflow as a minimal honest preview.",
        acceptance_criteria=["Explain the problem", "Explain the workflow"],
        max_files=4,
        max_total_bytes=80_000,
    )
    engine.record_build_spec(spec)
    bundle = BuildBundle(
        files=[
            BuildFile(
                path="index.html",
                content=(
                    "<!doctype html><html><body><h1>Faster quotes</h1>"
                    "<p>Capture details once and prepare the response faster.</p></body></html>"
                ),
            ),
            BuildFile(
                path="README.md",
                content="# Reviewed preview\n\nThis is a bounded local artifact without traction claims.",
            ),
        ]
    )
    run = BuildRun(spec_id=spec.id, opportunity_id=opportunity.id)
    sandbox = BuildSandbox(tmp_path / "workspaces")
    workspace = sandbox.write(run, bundle)
    run = run.model_copy(
        update={
            "status": BuildStatus.READY_PREVIEW,
            "workspace": str(workspace),
            "file_paths": [item.path for item in bundle.files],
        }
    )
    engine.record_build_run(run, event_type="build.ready_preview")
    engine.record_build_review(
        BuildReview(
            run_id=run.id,
            verdict=BuildReviewVerdict.PASS,
            score=9.0,
            summary="Artifact matches the validated problem and is safe for preview.",
        )
    )
    preview = PreviewManifest(
        run_id=run.id,
        opportunity_id=opportunity.id,
        workspace=str(workspace),
        entrypoint="index.html",
        artifact_hash=bundle_hash(bundle),
    )
    engine.record_preview_manifest(preview)
    return engine, opportunity, preview, run


def test_preview_publication_requires_run_scoped_approval_and_is_idempotent(tmp_path) -> None:
    engine, opportunity, preview, _ = _ready_preview(tmp_path)
    gateway = FakeGateway()
    publisher = PreviewPublisher(
        engine,
        workspace_root=tmp_path / "workspaces",
        gateway=gateway,
    )

    planned = publisher.prepare(opportunity.id)
    assert planned is not None
    assert planned.preview_id == preview.id
    assert planned.status == PreviewPublishStatus.WAITING_APPROVAL
    assert gateway.calls == 0

    same = publisher.prepare(opportunity.id)
    assert same is not None
    assert same.id == planned.id

    approved = publisher.approve(planned.id)
    assert approved.status == PreviewPublishStatus.READY
    publication = publisher.publish(approved.id)
    assert publication.preview_url == "https://demo.example.test/venture"
    assert publication.artifact_hash == preview.artifact_hash
    assert gateway.calls == 1
    assert publisher.state.get_run(approved.id).status == PreviewPublishStatus.PUBLISHED

    repeated = publisher.publish(approved.id)
    assert repeated.id == publication.id
    assert gateway.calls == 1


def test_tampered_artifact_is_blocked_before_gateway(tmp_path) -> None:
    engine, opportunity, _, build_run = _ready_preview(tmp_path)
    gateway = FakeGateway()
    publisher = PreviewPublisher(
        engine,
        workspace_root=tmp_path / "workspaces",
        gateway=gateway,
    )
    planned = publisher.prepare(opportunity.id)
    assert planned is not None
    approved = publisher.approve(planned.id)

    index = tmp_path / "workspaces" / str(opportunity.id) / str(build_run.id) / "index.html"
    index.write_text(index.read_text(encoding="utf-8") + "<!-- changed -->", encoding="utf-8")

    with pytest.raises(PreviewPublicationError, match="artifact hash mismatch"):
        publisher.publish(approved.id)
    assert gateway.calls == 0
    stored = publisher.state.get_run(approved.id)
    assert stored is not None
    assert stored.status == PreviewPublishStatus.BLOCKED


def test_publication_refuses_missing_passing_review(tmp_path) -> None:
    engine, opportunity, _, build_run = _ready_preview(tmp_path)
    with engine.store.connect() as db:
        db.execute("DELETE FROM build_reviews WHERE run_id = ?", (str(build_run.id),))
    gateway = FakeGateway()
    publisher = PreviewPublisher(
        engine,
        workspace_root=tmp_path / "workspaces",
        gateway=gateway,
    )
    planned = publisher.prepare(opportunity.id)
    assert planned is not None
    publisher.approve(planned.id)
    with pytest.raises(PreviewPublicationError, match="passing adversarial review"):
        publisher.publish(planned.id)
    assert gateway.calls == 0
