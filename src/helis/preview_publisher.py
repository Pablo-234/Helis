from __future__ import annotations

from pathlib import Path
from uuid import UUID

from helis.builder_sandbox import BuildSandbox, UnsafeBuildArtifact, bundle_hash
from helis.domain import (
    AuditEvent,
    BuildReviewVerdict,
    BuildStatus,
    PreviewManifest,
    VentureStage,
    utc_now,
)
from helis.engine import HelisEngine
from helis.policy import ActionKind, ActionRequest, AutonomyPolicy
from helis.preview_domain import PreviewPublishRun, PreviewPublishStatus, PublishedPreview
from helis.preview_gateway import PreviewGateway
from helis.preview_store import PreviewPublicationStore


class PreviewPublicationError(RuntimeError):
    pass


class PreviewPublisher:
    def __init__(
        self,
        engine: HelisEngine,
        *,
        workspace_root: str | Path = ".helis/workspaces",
        policy: AutonomyPolicy | None = None,
        gateway: PreviewGateway | None = None,
    ) -> None:
        self.engine = engine
        self.sandbox = BuildSandbox(workspace_root)
        self.policy = policy or AutonomyPolicy()
        self.gateway = gateway
        self.state = PreviewPublicationStore(engine.store)

    def prepare(self, opportunity_id: UUID | None = None) -> PreviewPublishRun | None:
        preview = self._target_preview(opportunity_id)
        if preview is None:
            return None
        existing = self.state.get_latest_for_preview(preview.id)
        if existing is not None:
            return existing

        decision = self.policy.evaluate(
            ActionRequest(
                kind=ActionKind.PUBLICATION,
                description=(
                    "publish reviewed HELIS preview artifact "
                    f"{preview.artifact_hash[:12]} for venture {preview.opportunity_id}"
                ),
            )
        )
        autonomous = decision.allowed and not decision.requires_approval
        run = PreviewPublishRun(
            preview_id=preview.id,
            opportunity_id=preview.opportunity_id,
            artifact_hash=preview.artifact_hash,
            status=(
                PreviewPublishStatus.READY
                if autonomous
                else PreviewPublishStatus.WAITING_APPROVAL
            ),
            approval_granted=autonomous,
        )
        self._save_run(run, "preview.publish_planned")
        return run

    def approve(self, run_id: UUID) -> PreviewPublishRun:
        run = self._require_run(run_id)
        if run.status == PreviewPublishStatus.PUBLISHED:
            return run
        if run.status != PreviewPublishStatus.WAITING_APPROVAL:
            raise PreviewPublicationError(
                f"publish run {run.id} cannot be approved from status {run.status.value}"
            )
        approved = run.model_copy(
            update={
                "approval_granted": True,
                "status": PreviewPublishStatus.READY,
                "updated_at": utc_now(),
            }
        )
        self._save_run(approved, "preview.publish_approved")
        return approved

    def publish(self, run_id: UUID) -> PublishedPreview:
        run = self._require_run(run_id)
        existing = self.state.get_publication_for_run(run.id)
        if existing is not None:
            self._mark_launched(existing)
            return existing
        if run.status != PreviewPublishStatus.READY or not run.approval_granted:
            raise PreviewPublicationError("preview publication requires a ready approved run")
        if self.gateway is None:
            raise PreviewPublicationError("preview gateway is not configured")

        preview = self._preview_for_run(run)
        build_run = self.engine.store.get_build_run(preview.run_id)
        if build_run is None or build_run.status != BuildStatus.READY_PREVIEW:
            return self._block(run, "source build is not READY_PREVIEW")
        review = self.engine.store.get_build_review(build_run.id)
        if review is None or review.verdict != BuildReviewVerdict.PASS:
            return self._block(run, "source build has no passing adversarial review")

        try:
            bundle = self.sandbox.read(build_run)
        except UnsafeBuildArtifact as exc:
            return self._block(run, f"cannot read reviewed artifact safely: {exc}")
        current_hash = bundle_hash(bundle)
        if current_hash != preview.artifact_hash or current_hash != run.artifact_hash:
            return self._block(run, "artifact hash mismatch after review")

        try:
            ack = self.gateway.execute(run, preview, bundle)
        except Exception as exc:
            failed = run.model_copy(
                update={
                    "status": PreviewPublishStatus.FAILED,
                    "error": f"{type(exc).__name__}: {exc}",
                    "updated_at": utc_now(),
                }
            )
            self._save_run(failed, "preview.publish_failed")
            raise PreviewPublicationError(failed.error) from exc

        published_run = run.model_copy(
            update={
                "status": PreviewPublishStatus.PUBLISHED,
                "destination": self.gateway.safe_destination,
                "external_ref": ack.dispatch_id,
                "updated_at": utc_now(),
            }
        )
        self._save_run(published_run, "preview.published")
        publication = PublishedPreview(
            run_id=run.id,
            preview_id=preview.id,
            opportunity_id=preview.opportunity_id,
            artifact_hash=preview.artifact_hash,
            preview_url=ack.preview_url,
            metadata=dict(ack.metadata),
        )
        self.state.save_publication(publication)
        self.engine.store.append_event(
            AuditEvent(
                event_type="preview.publication_recorded",
                entity_id=publication.id,
                data={
                    "run_id": str(run.id),
                    "opportunity_id": str(preview.opportunity_id),
                    "artifact_hash": preview.artifact_hash,
                    "preview_url": ack.preview_url,
                },
            )
        )
        self._mark_launched(publication)
        return publication

    def _mark_launched(self, publication: PublishedPreview) -> None:
        opportunity = self.engine.store.get_opportunity(publication.opportunity_id)
        if opportunity is None:
            raise PreviewPublicationError("published venture no longer exists")
        if opportunity.stage == VentureStage.LAUNCHED:
            return
        if opportunity.stage != VentureStage.READY_PREVIEW:
            raise PreviewPublicationError(
                "published preview can only promote READY_PREVIEW venture to LAUNCHED"
            )
        launched = opportunity.model_copy(update={"stage": VentureStage.LAUNCHED})
        self.engine.store.save_opportunity(launched)
        self.engine.store.append_event(
            AuditEvent(
                event_type="venture.launched",
                entity_id=opportunity.id,
                data={
                    "publication_id": str(publication.id),
                    "preview_url": publication.preview_url,
                    "artifact_hash": publication.artifact_hash,
                },
            )
        )

    def _block(self, run: PreviewPublishRun, reason: str) -> PublishedPreview:
        blocked = run.model_copy(
            update={
                "status": PreviewPublishStatus.BLOCKED,
                "error": reason,
                "updated_at": utc_now(),
            }
        )
        self._save_run(blocked, "preview.publish_blocked")
        raise PreviewPublicationError(reason)

    def _target_preview(self, opportunity_id: UUID | None) -> PreviewManifest | None:
        if opportunity_id is not None:
            opportunity = self.engine.store.get_opportunity(opportunity_id)
            if opportunity is None or opportunity.stage != VentureStage.READY_PREVIEW:
                return None
            return self.engine.store.get_preview_manifest_for_opportunity(opportunity_id)
        for opportunity in self.engine.store.list_opportunities():
            if opportunity.stage != VentureStage.READY_PREVIEW:
                continue
            preview = self.engine.store.get_preview_manifest_for_opportunity(opportunity.id)
            if preview is not None:
                return preview
        return None

    def _preview_for_run(self, run: PreviewPublishRun) -> PreviewManifest:
        preview = self.engine.store.get_preview_manifest_for_opportunity(run.opportunity_id)
        if preview is None or preview.id != run.preview_id:
            raise PreviewPublicationError("publish run no longer matches a persisted preview")
        if preview.artifact_hash != run.artifact_hash:
            raise PreviewPublicationError("publish run hash does not match preview manifest")
        return preview

    def _require_run(self, run_id: UUID) -> PreviewPublishRun:
        run = self.state.get_run(run_id)
        if run is None:
            raise PreviewPublicationError(f"preview publish run not found: {run_id}")
        return run

    def _save_run(self, run: PreviewPublishRun, event_type: str) -> None:
        self.state.save_run(run)
        self.engine.store.append_event(
            AuditEvent(
                event_type=event_type,
                entity_id=run.id,
                data={
                    "preview_id": str(run.preview_id),
                    "opportunity_id": str(run.opportunity_id),
                    "artifact_hash": run.artifact_hash,
                    "status": run.status.value,
                    "approval_granted": run.approval_granted,
                    "destination": run.destination,
                    "error": run.error,
                },
            )
        )
