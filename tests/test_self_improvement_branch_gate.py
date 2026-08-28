from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from helis.engine import HelisEngine
from helis.self_improvement_branch_domain import BranchMaterializationStatus
from helis.self_improvement_branch_gateway import BranchMaterializationAck
from helis.self_improvement_branch_manager import (
    SelfImprovementBranchError,
    SelfImprovementBranchManager,
)
from helis.self_improvement_branch_store import SelfImprovementBranchStore
from helis.self_improvement_domain import (
    CandidateFile,
    EvaluationSnapshot,
    ImprovementStatus,
    SelfImprovementEvaluation,
    SelfImprovementProposal,
)
from helis.self_improvement_sandbox import SelfImprovementSandbox
from helis.self_improvement_store import SelfImprovementStore
from helis.store import HelisStore

BASELINE = "def normalize(value: str) -> str:\n    return value.strip().lower()\n"
IMPROVED = "def normalize(value: str) -> str:\n    return ' '.join(value.strip().lower().split())\n"
BASE_REVISION = "a" * 40


@dataclass(slots=True)
class FakeBranchGateway:
    response: BranchMaterializationAck | None = None
    name: str = "fake_self_branch"
    safe_destination: str = "https://git.example.test/helis/branch"
    calls: int = 0

    def materialize(self, run, proposal, candidate, evaluation) -> BranchMaterializationAck:
        self.calls += 1
        if self.response is None:
            self.response = BranchMaterializationAck(
                candidate_hash=run.candidate_hash,
                base_revision=run.base_revision,
                branch_name=run.branch_name,
                base_file_hashes={item.path: item.original_sha256 for item in candidate.files},
                external_ref=f"branch:{run.branch_name}",
            )
        return self.response


def _engine(tmp_path: Path) -> HelisEngine:
    return HelisEngine(HelisStore(tmp_path / "helis.db"))


def _accepted_bundle(tmp_path: Path):
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
    return engine, sandbox_root, proposal, candidate, evaluation


def test_branch_requires_run_approval_and_is_idempotent(tmp_path) -> None:
    engine, sandbox_root, proposal, _, _ = _accepted_bundle(tmp_path)
    gateway = FakeBranchGateway()
    manager = SelfImprovementBranchManager(
        engine,
        gateway,
        sandbox_root=str(sandbox_root),
    )

    run = manager.prepare(proposal.id, base_revision=BASE_REVISION)

    assert run.status == BranchMaterializationStatus.WAITING_APPROVAL
    assert run.approval_granted is False
    assert gateway.calls == 0
    with pytest.raises(SelfImprovementBranchError, match="requires explicit run approval"):
        manager.materialize(run.id)
    assert gateway.calls == 0

    ready = manager.approve(run.id)
    assert ready.status == BranchMaterializationStatus.READY
    assert ready.approval_granted is True

    completed = manager.materialize(run.id)
    repeated = manager.materialize(run.id)

    assert completed.status == BranchMaterializationStatus.MATERIALIZED
    assert completed.external_ref == f"branch:{completed.branch_name}"
    assert repeated.id == completed.id
    assert gateway.calls == 1
    assert not hasattr(manager, "merge")


def test_candidate_mutation_after_approval_blocks_before_gateway(tmp_path) -> None:
    engine, sandbox_root, proposal, candidate, _ = _accepted_bundle(tmp_path)
    gateway = FakeBranchGateway()
    manager = SelfImprovementBranchManager(
        engine,
        gateway,
        sandbox_root=str(sandbox_root),
    )
    run = manager.prepare(proposal.id, base_revision=BASE_REVISION)
    manager.approve(run.id)
    source = Path(candidate.workspace) / "candidate/src/helis/dedup.py"
    source.write_text(IMPROVED + "\n# mutation after approval\n", encoding="utf-8")

    with pytest.raises(SelfImprovementBranchError, match="invalid after branch approval"):
        manager.materialize(run.id)

    stored = SelfImprovementBranchStore(engine.store).get(run.id)
    assert stored is not None
    assert stored.status == BranchMaterializationStatus.BLOCKED
    assert gateway.calls == 0


def test_gateway_must_attest_exact_base_file_hashes(tmp_path) -> None:
    engine, sandbox_root, proposal, candidate, _ = _accepted_bundle(tmp_path)
    gateway = FakeBranchGateway()
    manager = SelfImprovementBranchManager(
        engine,
        gateway,
        sandbox_root=str(sandbox_root),
    )
    run = manager.prepare(proposal.id, base_revision=BASE_REVISION)
    manager.approve(run.id)
    gateway.response = BranchMaterializationAck(
        candidate_hash=run.candidate_hash,
        base_revision=run.base_revision,
        branch_name=run.branch_name,
        base_file_hashes={candidate.files[0].path: "0" * 64},
        external_ref="branch:wrong-base",
    )

    with pytest.raises(SelfImprovementBranchError, match="base file hashes mismatch"):
        manager.materialize(run.id)

    stored = SelfImprovementBranchStore(engine.store).get(run.id)
    assert stored is not None
    assert stored.status == BranchMaterializationStatus.BLOCKED
    assert gateway.calls == 1


def test_prepare_rejects_non_accepted_proposal(tmp_path) -> None:
    engine, sandbox_root, proposal, _, _ = _accepted_bundle(tmp_path)
    state = SelfImprovementStore(engine.store)
    state.save_proposal(proposal.model_copy(update={"status": ImprovementStatus.REJECTED}))
    gateway = FakeBranchGateway()
    manager = SelfImprovementBranchManager(
        engine,
        gateway,
        sandbox_root=str(sandbox_root),
    )

    with pytest.raises(SelfImprovementBranchError, match="requires an accepted evaluated proposal"):
        manager.prepare(proposal.id, base_revision=BASE_REVISION)

    assert gateway.calls == 0
