from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from helis.budget import BudgetExceeded, CycleBudget
from helis.build_execution import (
    BuildExecutionBackend,
    BuildExecutionError,
    DockerBuildExecutionBackend,
)
from helis.build_templates import get_template
from helis.builder_generator import BuilderGenerator, BuildGenerationError
from helis.builder_planner import BuildPlanningError, BuilderPlanner
from helis.builder_repair import BuilderRepairer
from helis.builder_review import AdversarialBuildReviewer
from helis.builder_sandbox import BuildSandbox, BuildVerifier, UnsafeBuildArtifact, bundle_hash
from helis.domain import (
    BuildBundle,
    BuildCheck,
    BuildReview,
    BuildReviewVerdict,
    BuildRun,
    BuildSpec,
    BuildStatus,
    BuildTemplate,
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
    repair_attempted: bool = False


class BuilderMachine:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        *,
        workspace_root: str | Path = ".helis/workspaces",
        max_attempts: int = 2,
        execution_backend: BuildExecutionBackend | None = None,
    ) -> None:
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self.engine = engine
        self.budget = budget
        self.execution_backend = (
            execution_backend
            if execution_backend is not None
            else DockerBuildExecutionBackend.from_env()
        )
        enabled_templates = {BuildTemplate.STATIC_WEB, BuildTemplate.CONCIERGE_OPS}
        if self.execution_backend is not None:
            enabled_templates.add(BuildTemplate.PYTHON_SERVICE)
        self.planner = BuilderPlanner(
            provider,
            budget,
            enabled_templates=enabled_templates,
        )
        self.generator = BuilderGenerator(provider, budget)
        self.repairer = BuilderRepairer(provider, budget)
        self.reviewer = AdversarialBuildReviewer(provider, budget)
        self.verifier = BuildVerifier()
        self.sandbox = BuildSandbox(workspace_root)
        self.max_attempts = max_attempts

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
            except BuildPlanningError as exc:
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    blocked_reason=str(exc),
                )
            self.engine.record_build_spec(spec)
            opportunity = self.engine.store.get_opportunity(opportunity.id) or opportunity

        definition = get_template(spec.template)
        runs = self.engine.store.list_build_runs(spec_id=spec.id)
        run = runs[0] if runs else None
        failed_source: BuildRun | None = None
        repair_attempted = False
        if run is not None and run.status == BuildStatus.FAILED:
            if run.attempt >= self.max_attempts:
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=run,
                    blocked_reason=(
                        f"build repair budget exhausted after {run.attempt} attempts"
                    ),
                )
            failed_source = run
            repair_attempted = True
            run = BuildRun(
                spec_id=spec.id,
                opportunity_id=opportunity.id,
                attempt=failed_source.attempt + 1,
            )
            self.engine.record_build_run(run, event_type="build.repair_planned")

        if run is None:
            run = BuildRun(spec_id=spec.id, opportunity_id=opportunity.id)
            self.engine.record_build_run(run, event_type="build.run_planned")

        if (
            run.status == BuildStatus.PLANNED
            and definition.requires_execution
            and self.execution_backend is None
        ):
            return BuildTickReport(
                opportunity_id=opportunity.id,
                spec=spec,
                run=run,
                blocked_reason="executable build sandbox backend is not configured",
                repair_attempted=repair_attempted,
            )

        checks: list[BuildCheck] | None = None
        if run.status == BuildStatus.PLANNED:
            try:
                bundle = self._generate_bundle(
                    opportunity,
                    spec,
                    validation_results,
                    failed_source,
                )
            except BudgetExceeded:
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=run,
                    model_budget_exhausted=True,
                    repair_attempted=repair_attempted,
                )
            except BuildGenerationError as exc:
                failed = self._fail(run, str(exc), "build.generation_failed")
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    blocked_reason=str(exc),
                    repair_attempted=repair_attempted,
                )

            checks = self.verifier.verify(spec, bundle)
            for check in checks:
                check.run_id = run.id
                self.engine.record_build_check(check)
            if not all(check.passed for check in checks):
                failed = self._fail(
                    run,
                    "deterministic build verification failed",
                    "build.verification_failed",
                )
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    checks=checks,
                    blocked_reason=failed.error,
                    repair_attempted=repair_attempted,
                )

            try:
                workspace = self.sandbox.write(run, bundle)
            except UnsafeBuildArtifact as exc:
                failed = self._fail(run, str(exc), "build.sandbox_failed")
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    checks=checks,
                    blocked_reason=str(exc),
                    repair_attempted=repair_attempted,
                )

            if definition.requires_execution:
                assert self.execution_backend is not None
                try:
                    execution = self.execution_backend.execute(workspace)
                except BuildExecutionError as exc:
                    failed = self._fail(run, str(exc), "build.execution_failed")
                    return BuildTickReport(
                        opportunity_id=opportunity.id,
                        spec=spec,
                        run=failed,
                        checks=checks,
                        blocked_reason=str(exc),
                        repair_attempted=repair_attempted,
                    )
                execution_check = BuildCheck(
                    run_id=run.id,
                    name="sandbox_execution",
                    passed=execution.passed,
                    details=execution.details,
                )
                self.engine.record_build_check(execution_check)
                checks.append(execution_check)
                if not execution.passed:
                    failed = self._fail(
                        run,
                        "isolated executable build tests failed",
                        "build.execution_failed",
                    )
                    return BuildTickReport(
                        opportunity_id=opportunity.id,
                        spec=spec,
                        run=failed,
                        checks=checks,
                        blocked_reason=failed.error,
                        repair_attempted=repair_attempted,
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
                    repair_attempted=repair_attempted,
                )
            except UnsafeBuildArtifact as exc:
                failed = self._fail(run, str(exc), "build.sandbox_failed")
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    checks=checks,
                    blocked_reason=str(exc),
                    repair_attempted=repair_attempted,
                )

            self.engine.record_build_review(review)
            if review.verdict != BuildReviewVerdict.PASS:
                failed = self._fail(
                    run,
                    "adversarial review rejected the build",
                    "build.review_failed",
                )
                return BuildTickReport(
                    opportunity_id=opportunity.id,
                    spec=spec,
                    run=failed,
                    checks=checks,
                    review=review,
                    blocked_reason=failed.error,
                    repair_attempted=repair_attempted,
                )

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
                repair_attempted=repair_attempted,
            )

        return BuildTickReport(
            opportunity_id=opportunity.id,
            spec=spec,
            run=run,
            checks=checks,
            repair_attempted=repair_attempted,
        )

    def _generate_bundle(
        self,
        opportunity: Opportunity,
        spec: BuildSpec,
        validation_results: list,
        failed_source: BuildRun | None,
    ) -> BuildBundle:
        if failed_source is None:
            return self.generator.generate(opportunity, spec, validation_results)

        previous_bundle: BuildBundle | None = None
        if failed_source.workspace:
            try:
                previous_bundle = self.sandbox.read(failed_source)
            except UnsafeBuildArtifact:
                previous_bundle = None
        return self.repairer.repair(
            opportunity,
            spec,
            validation_results,
            failed_source,
            self.engine.store.list_build_checks(failed_source.id),
            self.engine.store.get_build_review(failed_source.id),
            previous_bundle,
        )

    def _fail(self, run: BuildRun, error: str, event_type: str) -> BuildRun:
        failed = run.model_copy(
            update={"status": BuildStatus.FAILED, "error": error, "updated_at": utc_now()}
        )
        self.engine.record_build_run(failed, event_type=event_type)
        return failed

    def _target(self, opportunity_id: UUID | None) -> Opportunity | None:
        if opportunity_id is not None:
            opportunity = self.engine.store.get_opportunity(opportunity_id)
            if opportunity is None:
                return None
            if opportunity.stage not in {
                VentureStage.VALIDATED,
                VentureStage.BUILDING,
                VentureStage.READY_PREVIEW,
            }:
                return None
            return opportunity

        for opportunity in self.engine.store.list_opportunities():
            if opportunity.stage not in {VentureStage.VALIDATED, VentureStage.BUILDING}:
                continue
            if self.engine.store.get_preview_manifest_for_opportunity(opportunity.id) is None:
                return opportunity
        return None
