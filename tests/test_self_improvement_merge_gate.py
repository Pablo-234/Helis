from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helis.engine import HelisEngine
from helis.self_improvement_branch_domain import (
    BranchMaterializationRun,
    BranchMaterializationStatus,
)
from helis.self_improvement_branch_store import SelfImprovementBranchStore
from helis.self_improvement_domain import (
    CandidateFile,
    EvaluationSnapshot,
    ImprovementStatus,
    SelfImprovementEvaluation,
    SelfImprovementProposal,
)
from helis.self_improvement_merge_domain import (
    SelfImprovementCIAttestation,
    SelfImprovementCICheck,
    SelfImprovementMergeStatus,
)
from helis.self_improvement_merge_gateway import SelfImprovementMergeAck
from helis.self_improvement_merge_manager import (
    SelfImprovementMergeError,
    SelfImprovementMergeManager,
    ci_attestation_hash,
)
from helis.self_improvement_merge_store import SelfImprovementMergeStore
from helis.self_improvement_sandbox import SelfImprovementSandbox
from helis.self_improvement_store import SelfImprovementStore
from helis.store import HelisStore

BASELINE = "def normalize(value: str) -> str:\n    return value.strip().lower()\n"
IMPROVED = "def normalize(value: str) -> str:\n    return ' '.join(value.strip().lower().split())\n"
BASE_REVISION = "a" * 40
HEAD_REVISION = "b" * 40
MERGED_REVISION = "c" * 40


@dataclass(slots=True)
class FakeCIGateway:
    responses: list[SelfImprovementCIAttestation] = field(default_factory=list)
    name: str = "fake_self_ci"
    safe_destination: str = "https://ci.example.test/helis"
    calls: int = 0

    def attest(self, run, branch_run, candidate) -> SelfImprovementCIAttestation:
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return _attestation(run, candidate)


@dataclass(slots=True)
class FakeMergeGateway:
    response: SelfImprovementMergeAck | None = None
    name: str = "fake_self_merge"
    safe_destination: str = "https://git.example.test/helis/merge"
    calls: int = 0

    def merge(self, run, branch_run, attestation) -> SelfImprovementMergeAck:
        self.calls += 1
        if self.response is None:
            self.response = SelfImprovementMergeAck(
                candidate_hash=run.candidate_hash,
                base_revision=run.base_revision,
                branch_name=run.branch_name,
                head_revision=attestation.head_revision,
                default_branch_before=run.base_revision,
                merged_commit_sha=MERGED_REVISION,
                external_ref=f"merge:{run.branch_name}",
            )
        return self.response


def _engine(tmp_path: Path) -> HelisEngine:
    return HelisEngine(HelisStore(tmp_path / "helis.db"))


def _materialized_bundle(tmp_path: Path):
    engine = _engine(tmp_path)
    sandbox_root = tmp_path / "self-workspaces"
    proposal = SelfImprovementProposal(
        objective="Improve deterministic dedup normalization without changing authority boundaries.",
        rationale=["Repeated whitespace can create avoidable duplicate variants."],
        target_files=["src/helis/dedup.py"],
        acceptance_criteria=["Normalization score improves on the immutable evaluator corpus."],
        metric_name="normalization_score",
        minimum_improvement=0.1,
        status=ImprovementStatus.WAITING_MERGE_APPROVAL,
    )
    candidate_file = CandidateFile(
        path="src/helis/dedup.py",
        original_sha256=hashlib.sha256(BASELINE.encode("utf-8")).hexdigest(),
        content=IMPROVED,
    )
    candidate = SelfImprovementSandbox(sandbox_root).write(proposal.id, [candidate_file])
    evaluation = SelfImprovementEvaluation(
        proposal_id=proposal.id,
        candidate_id=candidate.id,
        candidate_hash=candidate.candidate_hash,
        metric_name=proposal.metric_name,
        baseline=EvaluationSnapshot(passed=True, test_count=100, metric_value=0.5),
        candidate=EvaluationSnapshot(passed=True, test_count=100, metric_value=0.8),
        accepted=True,
        reason="candidate passed immutable tests and improved normalization_score by 0.3",
    )
    state = SelfImprovementStore(engine.store)
    state.save_proposal(proposal)
    state.save_candidate(candidate)
    state.save_evaluation(evaluation)
    branch_run = BranchMaterializationRun(
        proposal_id=proposal.id,
        candidate_id=candidate.id,
        evaluation_id=evaluation.id,
        candidate_hash=candidate.candidate_hash,
        base_revision=BASE_REVISION,
        branch_name=f"helis/self-test-{candidate.candidate_hash[:12]}",
        status=BranchMaterializationStatus.MATERIALIZED,
        approval_granted=True,
        external_ref="branch:review",
        destination="https://git.example.test/helis/branch",
    )
    SelfImprovementBranchStore(engine.store).save(branch_run)
    return engine, sandbox_root, proposal, candidate, branch_run


def _attestation(
    run,
    candidate,
    *,
    head_revision: str = HEAD_REVISION,
    checks: list[SelfImprovementCICheck] | None = None,
    attested_at: datetime | None = None,
) -> SelfImprovementCIAttestation:
    return SelfImprovementCIAttestation(
        candidate_hash=run.candidate_hash,
        base_revision=run.base_revision,
        branch_name=run.branch_name,
        head_revision=head_revision,
        candidate_file_hashes={
            item.path: hashlib.sha256(item.content.encode("utf-8")).hexdigest()
            for item in candidate.files
        },
        passed=True,
        test_count=123,
        checks=checks
        or [
            SelfImprovementCICheck(name="ruff", passed=True),
            SelfImprovementCICheck(name="pytest", passed=True),
        ],
        attested_at=attested_at or datetime.now(UTC),
    )


def test_merge_requires_green_ci_second_approval_and_is_idempotent(tmp_path) -> None:
    engine, sandbox_root, _, _, branch_run = _materialized_bundle(tmp_path)
    ci_gateway = FakeCIGateway()
    merge_gateway = FakeMergeGateway()
    manager = SelfImprovementMergeManager(
        engine,
        ci_gateway,
        merge_gateway,
        sandbox_root=str(sandbox_root),
    )

    run = manager.prepare(branch_run.id)
    assert run.status == SelfImprovementMergeStatus.WAITING_CI
    with pytest.raises(SelfImprovementMergeError, match="explicit second run approval"):
        manager.merge(run.id)
    assert merge_gateway.calls == 0

    attested = manager.attest_ci(run.id)
    assert attested.status == SelfImprovementMergeStatus.WAITING_APPROVAL
    assert attested.ci_attestation is not None
    assert ci_gateway.calls == 1

    ready = manager.approve(run.id)
    assert ready.status == SelfImprovementMergeStatus.READY
    assert ready.approval_granted is True

    merged = manager.merge(run.id)
    repeated = manager.merge(run.id)

    assert merged.status == SelfImprovementMergeStatus.MERGED
    assert merged.merged_commit_sha == MERGED_REVISION
    assert repeated.id == merged.id
    assert ci_gateway.calls == 2
    assert merge_gateway.calls == 1


def test_ci_attestation_hash_ignores_timestamp_and_check_order(tmp_path) -> None:
    engine, sandbox_root, _, candidate, branch_run = _materialized_bundle(tmp_path)
    manager = SelfImprovementMergeManager(engine, sandbox_root=str(sandbox_root))
    run = manager.prepare(branch_run.id)
    first = _attestation(run, candidate, attested_at=datetime(2026, 8, 28, tzinfo=UTC))
    second = _attestation(
        run,
        candidate,
        checks=[
            SelfImprovementCICheck(name="pytest", passed=True),
            SelfImprovementCICheck(name="RUFF", passed=True),
        ],
        attested_at=datetime(2026, 8, 28, tzinfo=UTC) + timedelta(minutes=5),
    )

    assert ci_attestation_hash(first) == ci_attestation_hash(second)


def test_branch_head_change_after_merge_approval_blocks_before_merge_gateway(tmp_path) -> None:
    engine, sandbox_root, _, candidate, branch_run = _materialized_bundle(tmp_path)
    manager_stub = SelfImprovementMergeManager(engine, sandbox_root=str(sandbox_root))
    run = manager_stub.prepare(branch_run.id)
    first = _attestation(run, candidate, head_revision=HEAD_REVISION)
    changed = _attestation(run, candidate, head_revision="d" * 40)
    ci_gateway = FakeCIGateway(responses=[first, changed])
    merge_gateway = FakeMergeGateway()
    manager = SelfImprovementMergeManager(
        engine,
        ci_gateway,
        merge_gateway,
        sandbox_root=str(sandbox_root),
    )

    manager.attest_ci(run.id)
    manager.approve(run.id)
    with pytest.raises(SelfImprovementMergeError, match="CI attestation changed"):
        manager.merge(run.id)

    stored = SelfImprovementMergeStore(engine.store).get(run.id)
    assert stored is not None
    assert stored.status == SelfImprovementMergeStatus.BLOCKED
    assert merge_gateway.calls == 0


def test_default_branch_advance_is_rejected_by_merge_attestation(tmp_path) -> None:
    engine, sandbox_root, _, candidate, branch_run = _materialized_bundle(tmp_path)
    manager_stub = SelfImprovementMergeManager(engine, sandbox_root=str(sandbox_root))
    run = manager_stub.prepare(branch_run.id)
    stable_first = _attestation(run, candidate)
    stable_second = _attestation(run, candidate)
    ci_gateway = FakeCIGateway(responses=[stable_first, stable_second])
    merge_gateway = FakeMergeGateway(
        response=SelfImprovementMergeAck(
            candidate_hash=run.candidate_hash,
            base_revision=run.base_revision,
            branch_name=run.branch_name,
            head_revision=HEAD_REVISION,
            default_branch_before="e" * 40,
            merged_commit_sha=MERGED_REVISION,
            external_ref="merge:stale-base",
        )
    )
    manager = SelfImprovementMergeManager(
        engine,
        ci_gateway,
        merge_gateway,
        sandbox_root=str(sandbox_root),
    )

    manager.attest_ci(run.id)
    manager.approve(run.id)
    with pytest.raises(SelfImprovementMergeError, match="default branch advanced"):
        manager.merge(run.id)

    stored = SelfImprovementMergeStore(engine.store).get(run.id)
    assert stored is not None
    assert stored.status == SelfImprovementMergeStatus.BLOCKED
    assert merge_gateway.calls == 1


def test_missing_or_failed_required_ci_check_cannot_reach_approval(tmp_path) -> None:
    engine, sandbox_root, _, candidate, branch_run = _materialized_bundle(tmp_path)
    manager_stub = SelfImprovementMergeManager(engine, sandbox_root=str(sandbox_root))
    run = manager_stub.prepare(branch_run.id)
    bad = _attestation(
        run,
        candidate,
        checks=[SelfImprovementCICheck(name="pytest", passed=True)],
    )
    ci_gateway = FakeCIGateway(responses=[bad])
    manager = SelfImprovementMergeManager(
        engine,
        ci_gateway,
        sandbox_root=str(sandbox_root),
    )

    with pytest.raises(SelfImprovementMergeError, match="missing required ruff/pytest"):
        manager.attest_ci(run.id)

    stored = SelfImprovementMergeStore(engine.store).get(run.id)
    assert stored is not None
    assert stored.status == SelfImprovementMergeStatus.WAITING_CI
    with pytest.raises(SelfImprovementMergeError, match="cannot approve"):
        manager.approve(run.id)
