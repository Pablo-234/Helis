from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from helis.budget import CycleBudget
from helis.domain import AuditEvent
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.self_improvement_domain import (
    EvaluationSnapshot,
    ImprovementStatus,
    SelfImprovementProposal,
)
from helis.self_improvement_evaluator import SelfImprovementEvaluationError
from helis.self_improvement_gateway import EvaluationGatewayResponse
from helis.self_improvement_machine import SelfImprovementMachine
from helis.self_improvement_planner import ImprovementSignalCollector, NoImprovementSignal
from helis.self_improvement_policy import SelfImprovementPolicy, UnsafeSelfImprovement
from helis.self_improvement_store import SelfImprovementStore
from helis.store import HelisStore


BASELINE = '''def normalize(value: str) -> str:\n    return value.strip().lower()\n'''
IMPROVED = '''def normalize(value: str) -> str:\n    return " ".join(value.strip().lower().split())\n'''


class FakeProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(
            content=json.dumps(self.payloads.pop(0)),
            prompt_tokens=10,
            completion_tokens=10,
        )


@dataclass(slots=True)
class FakeEvaluationGateway:
    response: EvaluationGatewayResponse | None = None
    name: str = "fake_self_eval"
    safe_destination: str = "https://eval.example.test/helis"
    calls: int = 0

    def evaluate(self, proposal, candidate) -> EvaluationGatewayResponse:
        self.calls += 1
        if self.response is None:
            self.response = _evaluation_response(candidate)
        return self.response


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    target = root / "src/helis/dedup.py"
    target.parent.mkdir(parents=True)
    target.write_text(BASELINE, encoding="utf-8")
    return root


def _plan_payload() -> dict:
    return {
        "objective": "Improve deterministic normalization of repeated whitespace in deduplication.",
        "rationale": ["Repeated spacing can create avoidable duplicate variants."],
        "target_files": ["src/helis/dedup.py"],
        "acceptance_criteria": ["Normalization score improves on the fixed evaluator corpus."],
        "metric_name": "normalization_score",
        "minimum_improvement": 0.1,
    }


def _replacement_payload(content: str = IMPROVED) -> dict:
    return {"files": [{"path": "src/helis/dedup.py", "content": content}]}


def _engine(tmp_path: Path) -> HelisEngine:
    return HelisEngine(HelisStore(tmp_path / "helis.db"))


def _machine(tmp_path: Path, provider, *, gateway=None) -> tuple[SelfImprovementMachine, Path]:
    repo = _repo(tmp_path)
    return (
        SelfImprovementMachine(
            _engine(tmp_path),
            provider,
            CycleBudget(max_model_calls=2, max_tokens=50_000),
            repo_root=repo,
            sandbox_root=tmp_path / "self-workspaces",
            evaluation_gateway=gateway,
        ),
        repo,
    )


def _evaluation_response(candidate, *, metric: float = 0.7, test_count: int = 100):
    return EvaluationGatewayResponse(
        candidate_hash=candidate.candidate_hash,
        metric_name="normalization_score",
        baseline_file_hashes={item.path: item.original_sha256 for item in candidate.files},
        baseline=EvaluationSnapshot(
            passed=True,
            test_count=100,
            metric_value=0.5,
            checks=["ruff", "pytest", "targeted normalization corpus"],
        ),
        candidate=EvaluationSnapshot(
            passed=True,
            test_count=test_count,
            metric_value=metric,
            checks=["ruff", "pytest", "targeted normalization corpus"],
        ),
        regressions=[],
    )


def test_no_signal_means_zero_model_calls(tmp_path) -> None:
    repo = _repo(tmp_path)
    provider = FakeProvider([])
    engine = _engine(tmp_path)
    machine = SelfImprovementMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=1),
        repo_root=repo,
        sandbox_root=tmp_path / "self-workspaces",
    )

    report = machine.tick()

    assert report.did_work is False
    assert "no recent failure/backoff signal" in report.reason
    assert provider.calls == 0


def test_signal_collector_uses_repeated_backoff_without_model(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.store.append_event(
        AuditEvent(
            event_type="portfolio.scheduler_backoff",
            data={"reason": "market_scan_no_new_signal", "consecutive_noops": 3},
        )
    )

    signals = ImprovementSignalCollector(engine).collect()

    assert len(signals) == 1
    assert "3x" in signals[0].summary


def test_materialization_never_changes_live_checkout_and_acceptance_cannot_merge(tmp_path) -> None:
    provider = FakeProvider([_plan_payload(), _replacement_payload()])
    gateway = FakeEvaluationGateway()
    machine, repo = _machine(tmp_path, provider, gateway=gateway)
    live = repo / "src/helis/dedup.py"
    before = live.read_text(encoding="utf-8")

    proposal = machine.propose("Improve dedup normalization without changing any authority boundary.")
    candidate = machine.materialize(proposal.id)

    assert live.read_text(encoding="utf-8") == before
    isolated = Path(candidate.workspace) / "candidate/src/helis/dedup.py"
    assert isolated.read_text(encoding="utf-8") == IMPROVED
    assert provider.calls == 2

    evaluation = machine.evaluate(proposal.id)
    refreshed = SelfImprovementStore(machine.engine.store).get_proposal(proposal.id)

    assert evaluation.accepted is True
    assert refreshed is not None
    assert refreshed.status == ImprovementStatus.WAITING_MERGE_APPROVAL
    assert gateway.calls == 1
    assert not hasattr(machine, "merge")
    assert live.read_text(encoding="utf-8") == before


def test_generator_rejects_new_imports(tmp_path) -> None:
    provider = FakeProvider(
        [
            _plan_payload(),
            _replacement_payload(
                "import os\n\ndef normalize(value: str) -> str:\n    return value.strip().lower()\n"
            ),
        ]
    )
    machine, repo = _machine(tmp_path, provider)
    proposal = machine.propose("Improve dedup normalization with a low-risk source-only change.")

    with pytest.raises(UnsafeSelfImprovement, match="may not add imports"):
        machine.materialize(proposal.id)

    assert (repo / "src/helis/dedup.py").read_text(encoding="utf-8") == BASELINE


def test_candidate_mutation_after_materialization_blocks_gateway(tmp_path) -> None:
    provider = FakeProvider([_plan_payload(), _replacement_payload()])
    gateway = FakeEvaluationGateway()
    machine, _ = _machine(tmp_path, provider, gateway=gateway)
    proposal = machine.propose("Improve dedup normalization with immutable candidate evaluation.")
    candidate = machine.materialize(proposal.id)
    isolated = Path(candidate.workspace) / "candidate/src/helis/dedup.py"
    isolated.write_text(IMPROVED + "\n# mutated after review boundary\n", encoding="utf-8")

    with pytest.raises(Exception, match="changed after materialization"):
        machine.evaluate(proposal.id)

    assert gateway.calls == 0


def test_evaluator_rejects_wrong_baseline_hash_before_credit(tmp_path) -> None:
    provider = FakeProvider([_plan_payload(), _replacement_payload()])
    gateway = FakeEvaluationGateway()
    machine, _ = _machine(tmp_path, provider, gateway=gateway)
    proposal = machine.propose("Improve dedup normalization with exact baseline attestation.")
    candidate = machine.materialize(proposal.id)
    response = _evaluation_response(candidate)
    response.baseline_file_hashes = {"src/helis/dedup.py": "0" * 64}
    gateway.response = response

    with pytest.raises(SelfImprovementEvaluationError, match="baseline hashes"):
        machine.evaluate(proposal.id)

    refreshed = SelfImprovementStore(machine.engine.store).get_proposal(proposal.id)
    assert refreshed is not None
    assert refreshed.status == ImprovementStatus.WAITING_EVALUATION


def test_evaluator_rejects_fewer_tests_or_insufficient_metric_gain(tmp_path) -> None:
    for suffix, response_factory, expected_reason in (
        (
            "fewer",
            lambda candidate: _evaluation_response(candidate, metric=0.8, test_count=99),
            "fewer tests",
        ),
        (
            "metric",
            lambda candidate: _evaluation_response(candidate, metric=0.55, test_count=100),
            "below required",
        ),
    ):
        case = tmp_path / suffix
        provider = FakeProvider([_plan_payload(), _replacement_payload()])
        gateway = FakeEvaluationGateway()
        machine, _ = _machine(case, provider, gateway=gateway)
        proposal = machine.propose("Improve dedup normalization only if measurable evidence supports it.")
        candidate = machine.materialize(proposal.id)
        gateway.response = response_factory(candidate)

        evaluation = machine.evaluate(proposal.id)
        refreshed = SelfImprovementStore(machine.engine.store).get_proposal(proposal.id)

        assert evaluation.accepted is False
        assert expected_reason in evaluation.reason
        assert refreshed is not None
        assert refreshed.status == ImprovementStatus.REJECTED


def test_policy_rejects_non_allowlisted_target(tmp_path) -> None:
    repo = _repo(tmp_path)
    proposal = SelfImprovementProposal(
        objective="Change the policy layer, which phase 5 must not permit.",
        rationale=["This is intentionally unsafe."],
        target_files=["src/helis/policy.py"],
        acceptance_criteria=["Never accepted."],
        metric_name="unsafe_score",
        minimum_improvement=1,
    )

    with pytest.raises(UnsafeSelfImprovement, match="outside the Phase-5 allowlist"):
        SelfImprovementPolicy().validate_proposal(proposal, repo)
