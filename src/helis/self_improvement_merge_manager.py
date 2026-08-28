from __future__ import annotations

import hashlib
import json
from uuid import UUID

from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.policy import ActionKind, ActionRequest, AutonomyPolicy
from helis.self_improvement_branch_domain import BranchMaterializationStatus
from helis.self_improvement_branch_store import SelfImprovementBranchStore
from helis.self_improvement_ci_gateway import SelfImprovementCIGateway
from helis.self_improvement_domain import ImprovementStatus
from helis.self_improvement_merge_domain import (
    SelfImprovementCIAttestation,
    SelfImprovementMergeRun,
    SelfImprovementMergeStatus,
)
from helis.self_improvement_merge_gateway import SelfImprovementMergeGateway
from helis.self_improvement_merge_store import SelfImprovementMergeStore
from helis.self_improvement_sandbox import SelfImprovementSandbox, UnsafeSelfImprovementWorkspace
from helis.self_improvement_store import SelfImprovementStore


class SelfImprovementMergeError(RuntimeError):
    pass


def ci_attestation_hash(attestation: SelfImprovementCIAttestation) -> str:
    payload = json.dumps(
        attestation.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SelfImprovementMergeManager:
    """Requires immutable green CI plus a second explicit approval before merge."""

    REQUIRED_CHECKS = frozenset({"ruff", "pytest"})

    def __init__(
        self,
        engine: HelisEngine,
        ci_gateway: SelfImprovementCIGateway | None = None,
        merge_gateway: SelfImprovementMergeGateway | None = None,
        *,
        sandbox_root: str = ".helis/self-improvement",
        policy: AutonomyPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.state = SelfImprovementStore(engine.store)
        self.branch_runs = SelfImprovementBranchStore(engine.store)
        self.runs = SelfImprovementMergeStore(engine.store)
        self.ci_gateway = ci_gateway
        self.merge_gateway = merge_gateway
        self.sandbox = SelfImprovementSandbox(sandbox_root)
        self.policy = policy or AutonomyPolicy()

    def prepare(self, branch_run_id: UUID) -> SelfImprovementMergeRun:
        branch_run = self._require_branch_run(branch_run_id)
        proposal, candidate, evaluation = self._accepted_bundle(branch_run.proposal_id)
        if branch_run.status != BranchMaterializationStatus.MATERIALIZED:
            raise SelfImprovementMergeError("merge preparation requires a materialized review branch")
        if not branch_run.external_ref:
            raise SelfImprovementMergeError("materialized review branch is missing its external reference")
        if branch_run.candidate_id != candidate.id or branch_run.candidate_hash != candidate.candidate_hash:
            raise SelfImprovementMergeError("review branch is not bound to the accepted candidate")

        existing = self.runs.get_for_branch_run(branch_run_id)
        if existing is not None:
            return existing
        run = SelfImprovementMergeRun(
            branch_run_id=branch_run.id,
            proposal_id=proposal.id,
            candidate_id=candidate.id,
            candidate_hash=candidate.candidate_hash,
            base_revision=branch_run.base_revision,
            branch_name=branch_run.branch_name,
            status=SelfImprovementMergeStatus.WAITING_CI,
        )
        self.runs.save(run)
        self._event(
            "self_improvement.merge_prepared",
            run.id,
            {
                "branch_run_id": str(branch_run.id),
                "evaluation_id": str(evaluation.id),
                "candidate_hash": run.candidate_hash,
                "base_revision": run.base_revision,
                "branch_name": run.branch_name,
                "second_approval_required": True,
            },
        )
        return run

    def attest_ci(self, run_id: UUID) -> SelfImprovementMergeRun:
        run = self._require_run(run_id)
        if run.status in {
            SelfImprovementMergeStatus.WAITING_APPROVAL,
            SelfImprovementMergeStatus.READY,
            SelfImprovementMergeStatus.MERGED,
        } and run.ci_attestation is not None:
            return run
        if run.status != SelfImprovementMergeStatus.WAITING_CI:
            raise SelfImprovementMergeError(f"cannot attest CI from {run.status.value}")
        if self.ci_gateway is None:
            raise SelfImprovementMergeError("self-improvement CI gateway is not configured")

        branch_run = self._require_branch_run(run.branch_run_id)
        _, candidate, _ = self._accepted_bundle(run.proposal_id)
        self._validate_branch_binding(run, branch_run, candidate)
        attestation = self.ci_gateway.attest(run, branch_run, candidate)
        self._validate_attestation(run, candidate, attestation)
        digest = ci_attestation_hash(attestation)
        updated = run.model_copy(
            update={
                "status": SelfImprovementMergeStatus.WAITING_APPROVAL,
                "ci_attestation": attestation,
                "ci_attestation_hash": digest,
                "updated_at": utc_now(),
            }
        )
        self.runs.save(updated)
        self._event(
            "self_improvement.ci_attested",
            run.id,
            {
                "head_revision": attestation.head_revision,
                "ci_attestation_hash": digest,
                "test_count": attestation.test_count,
                "checks": [item.name for item in attestation.checks],
            },
        )
        return updated

    def approve(self, run_id: UUID) -> SelfImprovementMergeRun:
        run = self._require_run(run_id)
        if run.status in {SelfImprovementMergeStatus.READY, SelfImprovementMergeStatus.MERGED}:
            if run.approval_granted:
                return run
        if run.status != SelfImprovementMergeStatus.WAITING_APPROVAL:
            raise SelfImprovementMergeError(f"cannot approve merge run from {run.status.value}")
        if run.ci_attestation is None or run.ci_attestation_hash is None:
            raise SelfImprovementMergeError("merge approval requires persisted green CI attestation")
        decision = self.policy.evaluate(
            ActionRequest(
                kind=ActionKind.SELF_MODIFY,
                description="Merge one exact green HELIS self-improvement review branch",
                reversible=False,
            )
        )
        updated = run.model_copy(
            update={
                "status": SelfImprovementMergeStatus.READY,
                "approval_granted": True,
                "updated_at": utc_now(),
            }
        )
        self.runs.save(updated)
        self._event(
            "self_improvement.merge_approved",
            run.id,
            {
                "candidate_hash": run.candidate_hash,
                "base_revision": run.base_revision,
                "branch_name": run.branch_name,
                "head_revision": run.ci_attestation.head_revision,
                "ci_attestation_hash": run.ci_attestation_hash,
                "policy_allowed_without_approval": decision.allowed,
                "policy_requires_approval": decision.requires_approval,
                "hard_second_approval_required": True,
            },
        )
        return updated

    def merge(self, run_id: UUID) -> SelfImprovementMergeRun:
        run = self._require_run(run_id)
        if run.status == SelfImprovementMergeStatus.MERGED and run.merged_commit_sha:
            return run
        if run.status != SelfImprovementMergeStatus.READY or not run.approval_granted:
            raise SelfImprovementMergeError("merge requires explicit second run approval")
        if run.ci_attestation is None or run.ci_attestation_hash is None:
            return self._block(run, "merge run lost its approved CI attestation")
        if self.ci_gateway is None:
            raise SelfImprovementMergeError("self-improvement CI gateway is not configured")
        if self.merge_gateway is None:
            raise SelfImprovementMergeError("self-improvement merge gateway is not configured")

        try:
            branch_run = self._require_branch_run(run.branch_run_id)
            _, candidate, _ = self._accepted_bundle(run.proposal_id)
            self._validate_branch_binding(run, branch_run, candidate)
        except (SelfImprovementMergeError, UnsafeSelfImprovementWorkspace) as exc:
            return self._block(run, f"approved merge bundle became invalid: {exc}")

        fresh = self.ci_gateway.attest(run, branch_run, candidate)
        self._validate_attestation(run, candidate, fresh)
        fresh_hash = ci_attestation_hash(fresh)
        if fresh_hash != run.ci_attestation_hash:
            return self._block(run, "CI attestation changed after merge approval")
        if fresh.head_revision != run.ci_attestation.head_revision:
            return self._block(run, "review branch head changed after merge approval")

        ack = self.merge_gateway.merge(run, branch_run, fresh)
        if ack.candidate_hash != run.candidate_hash:
            return self._block(run, "merge gateway candidate hash mismatch")
        if ack.base_revision.lower() != run.base_revision:
            return self._block(run, "merge gateway base revision mismatch")
        if ack.branch_name != run.branch_name:
            return self._block(run, "merge gateway branch name mismatch")
        if ack.head_revision.lower() != fresh.head_revision:
            return self._block(run, "merge gateway head revision mismatch")
        if ack.default_branch_before.lower() != run.base_revision:
            return self._block(run, "default branch advanced after candidate base revision")

        merged = run.model_copy(
            update={
                "status": SelfImprovementMergeStatus.MERGED,
                "merged_commit_sha": ack.merged_commit_sha.lower(),
                "external_ref": ack.external_ref,
                "destination": self.merge_gateway.safe_destination,
                "updated_at": utc_now(),
            }
        )
        self.runs.save(merged)
        self._event(
            "self_improvement.merged",
            run.id,
            {
                "candidate_hash": run.candidate_hash,
                "base_revision": run.base_revision,
                "branch_name": run.branch_name,
                "head_revision": fresh.head_revision,
                "merged_commit_sha": merged.merged_commit_sha,
                "external_ref": ack.external_ref,
            },
        )
        return merged

    def _validate_branch_binding(self, run, branch_run, candidate) -> None:
        if branch_run.status != BranchMaterializationStatus.MATERIALIZED:
            raise SelfImprovementMergeError("review branch is no longer materialized")
        if branch_run.id != run.branch_run_id:
            raise SelfImprovementMergeError("review branch run ID changed")
        if branch_run.candidate_id != candidate.id or branch_run.candidate_hash != run.candidate_hash:
            raise SelfImprovementMergeError("review branch candidate binding changed")
        if branch_run.base_revision != run.base_revision or branch_run.branch_name != run.branch_name:
            raise SelfImprovementMergeError("review branch base/name binding changed")

    def _validate_attestation(self, run, candidate, attestation) -> None:
        if attestation.candidate_hash != run.candidate_hash:
            raise SelfImprovementMergeError("CI attestation candidate hash mismatch")
        if attestation.base_revision != run.base_revision:
            raise SelfImprovementMergeError("CI attestation base revision mismatch")
        if attestation.branch_name != run.branch_name:
            raise SelfImprovementMergeError("CI attestation branch name mismatch")
        if attestation.head_revision == run.base_revision:
            raise SelfImprovementMergeError("CI attestation reports no candidate branch commit")
        expected_files = {
            item.path: hashlib.sha256(item.content.encode("utf-8")).hexdigest()
            for item in candidate.files
        }
        if attestation.candidate_file_hashes != expected_files:
            raise SelfImprovementMergeError("CI attestation candidate file hashes mismatch")
        if not attestation.passed or attestation.test_count <= 0:
            raise SelfImprovementMergeError("review branch CI is not green")
        checks = {item.name.lower(): item.passed for item in attestation.checks}
        if not self.REQUIRED_CHECKS <= set(checks):
            raise SelfImprovementMergeError("CI attestation is missing required ruff/pytest checks")
        if not all(checks[name] for name in self.REQUIRED_CHECKS):
            raise SelfImprovementMergeError("required ruff/pytest checks are not green")
        if not all(item.passed for item in attestation.checks):
            raise SelfImprovementMergeError("CI attestation contains a failed check")

    def _accepted_bundle(self, proposal_id: UUID):
        proposal = self.state.get_proposal(proposal_id)
        if proposal is None or proposal.status != ImprovementStatus.WAITING_MERGE_APPROVAL:
            raise SelfImprovementMergeError("proposal is no longer an accepted self-improvement")
        candidate = self.state.get_candidate_for_proposal(proposal_id)
        evaluation = self.state.get_evaluation_for_proposal(proposal_id)
        if candidate is None or evaluation is None or not evaluation.accepted:
            raise SelfImprovementMergeError("accepted self-improvement bundle is incomplete")
        if evaluation.candidate_id != candidate.id or evaluation.candidate_hash != candidate.candidate_hash:
            raise SelfImprovementMergeError("evaluation no longer matches the candidate")
        self.sandbox.verify(candidate)
        return proposal, candidate, evaluation

    def _require_branch_run(self, branch_run_id: UUID):
        branch_run = self.branch_runs.get(branch_run_id)
        if branch_run is None:
            raise SelfImprovementMergeError(f"branch materialization run not found: {branch_run_id}")
        return branch_run

    def _require_run(self, run_id: UUID) -> SelfImprovementMergeRun:
        run = self.runs.get(run_id)
        if run is None:
            raise SelfImprovementMergeError(f"self-improvement merge run not found: {run_id}")
        return run

    def _block(self, run: SelfImprovementMergeRun, reason: str) -> SelfImprovementMergeRun:
        blocked = run.model_copy(
            update={
                "status": SelfImprovementMergeStatus.BLOCKED,
                "error": reason,
                "updated_at": utc_now(),
            }
        )
        self.runs.save(blocked)
        self._event(
            "self_improvement.merge_blocked",
            run.id,
            {"reason": reason, "candidate_hash": run.candidate_hash},
        )
        raise SelfImprovementMergeError(reason)

    def _event(self, event_type: str, entity_id: UUID, data: dict) -> None:
        self.engine.store.append_event(
            AuditEvent(event_type=event_type, entity_id=entity_id, data=data)
        )
