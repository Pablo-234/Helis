from __future__ import annotations

from uuid import UUID

from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.policy import ActionKind, ActionRequest, AutonomyPolicy
from helis.self_improvement_branch_domain import (
    BranchMaterializationRun,
    BranchMaterializationStatus,
)
from helis.self_improvement_branch_gateway import SelfImprovementBranchGateway
from helis.self_improvement_branch_store import SelfImprovementBranchStore
from helis.self_improvement_domain import ImprovementStatus
from helis.self_improvement_sandbox import (
    SelfImprovementSandbox,
    UnsafeSelfImprovementWorkspace,
)
from helis.self_improvement_store import SelfImprovementStore


class SelfImprovementBranchError(RuntimeError):
    pass


class SelfImprovementBranchManager:
    """Materializes an accepted candidate onto a review branch only after explicit approval."""

    def __init__(
        self,
        engine: HelisEngine,
        gateway: SelfImprovementBranchGateway | None = None,
        *,
        sandbox_root: str = ".helis/self-improvement",
        policy: AutonomyPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.state = SelfImprovementStore(engine.store)
        self.runs = SelfImprovementBranchStore(engine.store)
        self.gateway = gateway
        self.sandbox = SelfImprovementSandbox(sandbox_root)
        self.policy = policy or AutonomyPolicy()

    def prepare(self, proposal_id: UUID, *, base_revision: str) -> BranchMaterializationRun:
        proposal, candidate, evaluation = self._accepted_bundle(proposal_id)
        existing = self.runs.get_for_proposal(proposal_id)
        normalized_revision = base_revision.lower()
        if existing is not None:
            if (
                existing.base_revision != normalized_revision
                or existing.candidate_hash != candidate.candidate_hash
            ):
                raise SelfImprovementBranchError(
                    "existing branch run is bound to a different base revision or candidate"
                )
            return existing

        decision = self.policy.evaluate(
            ActionRequest(
                kind=ActionKind.SELF_MODIFY,
                description=(
                    "Materialize one evaluated HELIS self-improvement candidate onto a review branch"
                ),
                reversible=True,
            )
        )
        run = BranchMaterializationRun(
            proposal_id=proposal.id,
            candidate_id=candidate.id,
            evaluation_id=evaluation.id,
            candidate_hash=candidate.candidate_hash,
            base_revision=normalized_revision,
            branch_name=self._branch_name(proposal.id, candidate.candidate_hash),
            status=BranchMaterializationStatus.WAITING_APPROVAL,
            approval_granted=False,
        )
        self.runs.save(run)
        self._event(
            "self_improvement.branch_prepared",
            run.id,
            {
                "proposal_id": str(proposal.id),
                "candidate_id": str(candidate.id),
                "candidate_hash": candidate.candidate_hash,
                "base_revision": run.base_revision,
                "branch_name": run.branch_name,
                "policy_allowed_without_approval": decision.allowed,
                "policy_requires_approval": decision.requires_approval,
                "hard_run_approval_required": True,
            },
        )
        return run

    def approve(self, run_id: UUID) -> BranchMaterializationRun:
        run = self._require_run(run_id)
        if run.status in {
            BranchMaterializationStatus.READY,
            BranchMaterializationStatus.MATERIALIZED,
        } and run.approval_granted:
            return run
        if run.status != BranchMaterializationStatus.WAITING_APPROVAL:
            raise SelfImprovementBranchError(f"cannot approve branch run from {run.status.value}")
        updated = run.model_copy(
            update={
                "status": BranchMaterializationStatus.READY,
                "approval_granted": True,
                "updated_at": utc_now(),
            }
        )
        self.runs.save(updated)
        self._event(
            "self_improvement.branch_approved",
            run.id,
            {
                "proposal_id": str(run.proposal_id),
                "candidate_hash": run.candidate_hash,
                "base_revision": run.base_revision,
                "branch_name": run.branch_name,
            },
        )
        return updated

    def materialize(self, run_id: UUID) -> BranchMaterializationRun:
        run = self._require_run(run_id)
        if run.status == BranchMaterializationStatus.MATERIALIZED and run.external_ref:
            return run
        if run.status != BranchMaterializationStatus.READY or not run.approval_granted:
            raise SelfImprovementBranchError("branch materialization requires explicit run approval")

        try:
            proposal, candidate, evaluation = self._accepted_bundle(run.proposal_id)
        except (SelfImprovementBranchError, UnsafeSelfImprovementWorkspace) as exc:
            return self._block(run, f"accepted bundle invalid after branch approval: {exc}")
        if candidate.id != run.candidate_id or evaluation.id != run.evaluation_id:
            return self._block(run, "accepted bundle changed after branch approval")
        if candidate.candidate_hash != run.candidate_hash:
            return self._block(run, "candidate hash changed after branch approval")
        if self.gateway is None:
            raise SelfImprovementBranchError("self-improvement branch gateway is not configured")

        try:
            ack = self.gateway.materialize(run, proposal, candidate, evaluation)
        except Exception as exc:
            failed = run.model_copy(
                update={
                    "status": BranchMaterializationStatus.FAILED,
                    "error": str(exc),
                    "updated_at": utc_now(),
                }
            )
            self.runs.save(failed)
            self._event(
                "self_improvement.branch_failed",
                run.id,
                {"error": str(exc), "candidate_hash": run.candidate_hash},
            )
            raise

        expected_hashes = {item.path: item.original_sha256 for item in candidate.files}
        if ack.candidate_hash != run.candidate_hash:
            return self._block(run, "branch gateway candidate hash mismatch")
        if ack.base_revision.lower() != run.base_revision:
            return self._block(run, "branch gateway base revision mismatch")
        if ack.branch_name != run.branch_name:
            return self._block(run, "branch gateway branch name mismatch")
        if ack.base_file_hashes != expected_hashes:
            return self._block(run, "branch gateway base file hashes mismatch")

        completed = run.model_copy(
            update={
                "status": BranchMaterializationStatus.MATERIALIZED,
                "external_ref": ack.external_ref,
                "destination": self.gateway.safe_destination,
                "updated_at": utc_now(),
            }
        )
        self.runs.save(completed)
        self._event(
            "self_improvement.branch_materialized",
            run.id,
            {
                "proposal_id": str(run.proposal_id),
                "candidate_hash": run.candidate_hash,
                "base_revision": run.base_revision,
                "branch_name": run.branch_name,
                "external_ref": ack.external_ref,
                "merge_available": False,
            },
        )
        return completed

    def _accepted_bundle(self, proposal_id: UUID):
        proposal = self.state.get_proposal(proposal_id)
        if proposal is None:
            raise SelfImprovementBranchError(f"self-improvement proposal not found: {proposal_id}")
        if proposal.status != ImprovementStatus.WAITING_MERGE_APPROVAL:
            raise SelfImprovementBranchError(
                "branch materialization requires an accepted evaluated proposal"
            )
        candidate = self.state.get_candidate_for_proposal(proposal_id)
        evaluation = self.state.get_evaluation_for_proposal(proposal_id)
        if candidate is None or evaluation is None or not evaluation.accepted:
            raise SelfImprovementBranchError("accepted self-improvement bundle is incomplete")
        if evaluation.candidate_id != candidate.id:
            raise SelfImprovementBranchError("evaluation candidate does not match stored candidate")
        if evaluation.candidate_hash != candidate.candidate_hash:
            raise SelfImprovementBranchError("evaluation hash does not match stored candidate")
        self.sandbox.verify(candidate)
        return proposal, candidate, evaluation

    def _require_run(self, run_id: UUID) -> BranchMaterializationRun:
        run = self.runs.get(run_id)
        if run is None:
            raise SelfImprovementBranchError(f"branch materialization run not found: {run_id}")
        return run

    def _block(self, run: BranchMaterializationRun, reason: str) -> BranchMaterializationRun:
        blocked = run.model_copy(
            update={
                "status": BranchMaterializationStatus.BLOCKED,
                "error": reason,
                "updated_at": utc_now(),
            }
        )
        self.runs.save(blocked)
        self._event(
            "self_improvement.branch_blocked",
            run.id,
            {"reason": reason, "candidate_hash": run.candidate_hash},
        )
        raise SelfImprovementBranchError(reason)

    @staticmethod
    def _branch_name(proposal_id: UUID, candidate_hash: str) -> str:
        return f"helis/self-{str(proposal_id).split('-')[0]}-{candidate_hash[:12]}"

    def _event(self, event_type: str, entity_id: UUID, data: dict) -> None:
        self.engine.store.append_event(
            AuditEvent(event_type=event_type, entity_id=entity_id, data=data)
        )
