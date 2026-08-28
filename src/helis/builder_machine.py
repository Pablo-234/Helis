from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from helis.budget import BudgetExceeded, CycleBudget
from helis.builder_generator import BuildGenerationError, BuilderGenerator
from helis.builder_planner import BuilderPlanner
from helis.builder_review import AdversarialBuildReviewer
from helis.builder_sandbox import BuildSandbox, BuildVerifier, UnsafeBuildArtifact, bundle_hash
from helis.build_templates import get_template
from helis.domain import (
    BuildCheck,
    BuildReview,
    BuildReviewVerdict,
    BuildRun,
    BuildStatus,
    BuildSpec,
    Opportunity,
    PreviewManifest,
    VentureStage,
    utc_now,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider


@dataclass(slots=True)
class BuildTickReport:
    opportunity_id: UUID | None = None
    spec: BuildSpec | None = None
    run: BuildRun | None = None
    checks: list[BuildCheck] | None = None
    review: BuildReview | None = None
    preview: PreviewManifest | None = None
    model_budget_exhausted: bool = False
    blocked_reason: str | None = None


class BuilderMachine:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        *,
        workspace_root: str | Path = ".helis/workspaces",
    ) -> None:
        self.engine = engine
        self.budget = budget
        self.planner = BuilderPlanner(provider, budget)
        self.generator = BuilderGenerator(provider, budget)
        self.reviewer = AdversarialBuildReviewer(provider, budget)
        self.verifier = BuildVerifier()
        self.sandbox = BuildSandbox(workspace_root)

    def tick(self, opportunity_id: UUID | None = None) -> BuildTickReport:
        opportunity = self._target(opportunity_id)
        if opportunity is None:
            return BuildTickReport()

        existing_preview = self.engine.store.get_preview_manifest_for_opportunity(opportunity.id)
        if existing_preview is not None:
            return BuildTickReport(
                opportunity_id=opportunity.id,
                preview=existing_preview,
                run=self.engine.store.get_build_run(existing_preview.run_id),
            )

        validation_results = self.engine.store.list_validation_results(opportunity.id)
        spec = self.engine.store.get_build_spec_for_opportunity(opportunity.id)
        if spec is None:
            try:
                spec = self.planner.plan(opportunity, validation_results)
            except BudgetExceeded:
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    model_budget_exhausted=True,
                )
            self.engine.record_build_spec(spec)
            opportunity = self.engine.store.get_opportunity(opportunity.id) or opportunity

        runs = self.engine.store.list_build_runs(spec_id=spec.id)
        run = runs[0] if runs else None
        if run is not None and run.status == BuildStatus.FAILED:
            return BuildTickReport(
                opportunity_id=opportunity.id,
                spec=spec,
                run=run,
                blocked_reason="latest build failed; bounded repair loop is not enabled yet",
            )

        if run is None:
            run = BuildRun(spec_id=spec.id, opportunity_id=opportunity.id)
            self.engine.record_build_run(run, event_type="build.run_planned")

        checks: list[BuildCheck] | None = None
        if run.status == BuildStatus.PLANNED:
            try:
                bundle = self.generator.generate(opportunity, spec, validation_results)
            except BudgetExceeded:
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=run,
                    model_budget_exhausted=True,
                )
            except BuildGenerationError as exc:
                failed = run.model_copy(
                    update={"status": BuildStatus.FAILED, "error": str(exc), "updated_at": utc_now()}
                )
                self.engine.record_build_run(failed, event_type="build.generation_failed")
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    blocked_reason=str(exc),
                )

            checks = self.verifier.verify(spec, bundle)
            for check in checks:
                check.run_id = run.id
                self.engine.record_build_check(check)
            if not all(check.passed for check in checks):
                failed = run.model_copy(
                    update={
                        "status": BuildStatus.FAILED,
                        "error": "deterministic build verification failed",
                        "updated_at": utc_now(),
                    }
                )
                self.engine.record_build_run(failed, event_type="build.verification_failed")
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    checks=checks,
                    blocked_reason=failed.error,
                )

            try:
                workspace = self.sandbox.write(run, bundle)
            except UnsafeBuildArtifact as exc:
                failed = run.model_copy(
                    update={"status": BuildStatus.FAILED, "error": str(exc), "updated_at": utc_now()}
                )
                self.engine.record_build_run(failed, event_type="build.sandbox_failed")
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    checks=checks,
                    blocked_reason=str(exc),
                )
            run = run.model_copy(
                update={
                    "status": BuildStatus.VERIFIED,
                    "workspace": str(workspace),
                    "file_paths": [item.path for item in bundle.files],
                    "updated_at": utc_now(),
                }
            )
            self.engine.record_build_run(run, event_type="build.verified")

        if run.status == BuildStatus.VERIFIED:
            try:
                bundle = self.sandbox.read(run)
                review = self.reviewer.review(opportunity, spec, run, bundle)
            except BudgetExceeded:
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=run,
                    checks=checks,
                    model_budget_exhausted=True,
                )
            except UnsafeBuildArtifact as exc:
                failed = run.model_copy(
                    update={"status": BuildStatus.FAILED, "error": str(exc), "updated_at": utc_now()}
                )
                self.engine.record_build_run(failed, event_type="build.sandbox_failed")
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    checks=checks,
                    blocked_reason=str(exc),
                )

            self.engine.record_build_review(review)
            if review.verdict != BuildReviewVerdict.PASS:
                failed = run.model_copy(
                    update={
                        "status": BuildStatus.FAILED,
                        "error": "adversarial review rejected the build",
                        "updated_at": utc_now(),
                    }
                )
                self.engine.record_build_run(failed, event_type="build.review_failed")
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    checks=checks,
                    review=review,
                    blocked_reason=failed.error,
                )

            definition = get_template(spec.template)
            preview = PreviewManifest(
                run_id=run.id,
                opportunity_id=opportunity.id,
                workspace=run.workspace or "",
                entrypoint=definition.entrypoint,
                artifact_hash=bundle_hash(bundle),
            )
            ready = run.model_copy(
                update={
                    "status": BuildStatus.READY_PREVIEW,
                    "completed_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            self.engine.record_build_run(ready, event_type="build.ready_preview")
            self.engine.record_preview_manifest(preview)
            return BuildTickReport(
                opportunity_id=opportunity.id,
                spec=spec,
                run=ready,
                checks=checks,
                review=review,
                preview=preview,
            )

        return BuildTickReport(opportunity_id=opportunity.id, spec=spec, run=run, checks=checks)

    def _target(self, opportunity_id: UUID | None) -> Opportunity | None:
        if opportunity_id is not None:
            opportunity = self.engine.store.get_opportunity(opportunity_id)
            if opportunity is None:
                return None
            if opportunity.stage not in {VentureStage.VALIDATED, VentureStage.BUILDING}:
                return None
            return opportunity

        for opportunity in self.engine.store.list_opportunities():
            if opportunity.stage not in {VentureStage.VALIDATED, VentureStage.BUILDING}:
                continue
            if self.engine.store.get_preview_manifest_for_opportunity(opportunity.id) is None:
                return opportunity
        return None
