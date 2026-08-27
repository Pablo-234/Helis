from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from helis.budget import BudgetExceeded, CycleBudget
from helis.build_domain import (
    BuildRun,
    BuildRunStatus,
    BuildSpec,
    SandboxReport,
    SandboxStatus,
)
from helis.build_generator import BuildGenerator, BundleLimits
from helis.build_planner import BuildPlanner
from helis.build_store import BuildStore
from helis.domain import AuditEvent, Opportunity, VentureStage, utc_now
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.sandbox import verifier_for
from helis.workspace import WorkspaceManager


@dataclass(slots=True)
class BuilderTickReport:
    opportunity_id: UUID | None
    spec: BuildSpec | None = None
    run: BuildRun | None = None
    sandbox: SandboxReport | None = None


class BuilderMachine:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        model_budget: CycleBudget,
        *,
        workspace_root: str | Path = "helis-workspaces",
        bundle_limits: BundleLimits | None = None,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.model_budget = model_budget
        self.build_store = BuildStore(engine.store)
        self.planner = BuildPlanner(provider, model_budget)
        self.generator = BuildGenerator(provider, model_budget, bundle_limits)
        self.workspaces = WorkspaceManager(workspace_root)

    def tick(self, opportunity_id: UUID | None = None) -> BuilderTickReport:
        opportunity = self._target(opportunity_id)
        if opportunity is None:
            return BuilderTickReport(opportunity_id=None)

        spec = self.build_store.get_spec(opportunity.id)
        if spec is None:
            spec = self.planner.plan(
                opportunity,
                self.engine.store.list_validation_results(opportunity.id),
            )
            self.build_store.save_spec(spec)
            self.engine.store.append_event(
                AuditEvent(
                    event_type="build.spec_planned",
                    entity_id=spec.id,
                    data={
                        "opportunity_id": str(opportunity.id),
                        "runtime": spec.runtime.value,
                    },
                )
            )
            opportunity = self._claim_for_build(opportunity)

        run = self.build_store.get_latest_run(opportunity.id)
        if run is None:
            run = BuildRun(spec_id=spec.id, opportunity_id=opportunity.id)
            self._record_run(run, "build.run_planned")

        if run.status in {BuildRunStatus.TESTED, BuildRunStatus.FAILED, BuildRunStatus.BLOCKED}:
            return BuilderTickReport(
                opportunity_id=opportunity.id,
                spec=spec,
                run=run,
                sandbox=run.sandbox,
            )

        if run.status in {BuildRunStatus.PLANNED, BuildRunStatus.GENERATING}:
            generating = run.model_copy(
                update={"status": BuildRunStatus.GENERATING, "updated_at": utc_now(), "error": None}
            )
            self._record_run(generating, "build.generating")
            try:
                bundle, _ = self.generator.generate(spec)
                snapshot = self.workspaces.create(generating.id, spec, bundle)
            except BudgetExceeded:
                deferred = generating.model_copy(
                    update={"status": BuildRunStatus.PLANNED, "updated_at": utc_now(), "error": None}
                )
                self._record_run(deferred, "build.deferred_budget")
                raise
            except Exception as exc:  # noqa: BLE001 -- generated-build boundary is fail-closed
                failed = generating.model_copy(
                    update={
                        "status": BuildRunStatus.FAILED,
                        "error": f"{type(exc).__name__}: {exc}",
                        "updated_at": utc_now(),
                    }
                )
                self._record_run(failed, "build.failed")
                return BuilderTickReport(opportunity_id=opportunity.id, spec=spec, run=failed)
            run = generating.model_copy(
                update={
                    "status": BuildRunStatus.GENERATED,
                    "workspace_path": str(snapshot.path),
                    "file_count": snapshot.file_count,
                    "total_bytes": snapshot.total_bytes,
                    "bundle_digest": snapshot.digest,
                    "updated_at": utc_now(),
                }
            )
            self._record_run(run, "build.generated")

        if run.status in {BuildRunStatus.GENERATED, BuildRunStatus.TESTING}:
            if not run.workspace_path:
                broken = run.model_copy(
                    update={
                        "status": BuildRunStatus.FAILED,
                        "error": "generated build has no workspace path",
                        "updated_at": utc_now(),
                    }
                )
                self._record_run(broken, "build.failed")
                return BuilderTickReport(opportunity_id=opportunity.id, spec=spec, run=broken)
            testing = run.model_copy(
                update={"status": BuildRunStatus.TESTING, "updated_at": utc_now(), "error": None}
            )
            self._record_run(testing, "build.testing")
            try:
                report = verifier_for(spec.runtime).verify(Path(testing.workspace_path))
            except Exception as exc:  # noqa: BLE001 -- verifier must never fall back to host behavior
                report = SandboxReport(
                    status=SandboxStatus.BLOCKED,
                    stderr=f"verifier unavailable: {type(exc).__name__}: {exc}",
                    verifier="builder_fail_closed_boundary",
                )
            status = {
                SandboxStatus.PASSED: BuildRunStatus.TESTED,
                SandboxStatus.FAILED: BuildRunStatus.FAILED,
                SandboxStatus.BLOCKED: BuildRunStatus.BLOCKED,
            }[report.status]
            completed = testing.model_copy(
                update={
                    "status": status,
                    "sandbox": report,
                    "error": None if status == BuildRunStatus.TESTED else report.stderr,
                    "updated_at": utc_now(),
                }
            )
            self._record_run(
                completed,
                "build.tested" if status == BuildRunStatus.TESTED else "build.verification_failed",
            )
            return BuilderTickReport(
                opportunity_id=opportunity.id,
                spec=spec,
                run=completed,
                sandbox=report,
            )

        return BuilderTickReport(opportunity_id=opportunity.id, spec=spec, run=run)

    def _claim_for_build(self, opportunity: Opportunity) -> Opportunity:
        if opportunity.stage != VentureStage.VALIDATED:
            raise ValueError("only validated ventures can enter the builder")
        building = opportunity.model_copy(update={"stage": VentureStage.BUILDING})
        self.engine.store.save_opportunity(building)
        self.engine.store.append_event(
            AuditEvent(
                event_type="build.claimed",
                entity_id=opportunity.id,
                data={
                    "from_stage": VentureStage.VALIDATED.value,
                    "to_stage": VentureStage.BUILDING.value,
                },
            )
        )
        return building

    def _record_run(self, run: BuildRun, event_type: str) -> None:
        self.build_store.save_run(run)
        self.engine.store.append_event(
            AuditEvent(
                event_type=event_type,
                entity_id=run.id,
                data={
                    "opportunity_id": str(run.opportunity_id),
                    "spec_id": str(run.spec_id),
                    "status": run.status.value,
                    "workspace_path": run.workspace_path,
                    "error": run.error,
                },
            )
        )

    def _target(self, opportunity_id: UUID | None) -> Opportunity | None:
        allowed = {VentureStage.VALIDATED, VentureStage.BUILDING}
        if opportunity_id is not None:
            candidate = self.engine.store.get_opportunity(opportunity_id)
            if candidate is None or candidate.stage not in allowed:
                return None
            return candidate

        for item in self.engine.ranked_queue():
            if item.opportunity.stage in allowed:
                return item.opportunity
        for candidate in self.engine.store.list_opportunities():
            if candidate.stage in allowed:
                return candidate
        return None
