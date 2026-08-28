from __future__ import annotations

from helis.self_improvement_domain import (
    SelfImprovementCandidate,
    SelfImprovementEvaluation,
    SelfImprovementProposal,
)
from helis.self_improvement_gateway import (
    EvaluationGatewayResponse,
    SelfImprovementEvaluationGateway,
)
from helis.self_improvement_sandbox import SelfImprovementSandbox


class SelfImprovementEvaluationError(RuntimeError):
    pass


class SelfImprovementEvaluator:
    def __init__(
        self,
        gateway: SelfImprovementEvaluationGateway | None,
        *,
        sandbox_root: str = ".helis/self-improvement",
    ) -> None:
        self.gateway = gateway
        self.sandbox = SelfImprovementSandbox(sandbox_root)

    def evaluate(
        self,
        proposal: SelfImprovementProposal,
        candidate: SelfImprovementCandidate,
    ) -> SelfImprovementEvaluation:
        self.sandbox.verify(candidate)
        if self.gateway is None:
            raise SelfImprovementEvaluationError("self-improvement evaluation gateway is not configured")
        response = self.gateway.evaluate(proposal, candidate)
        if response.candidate_hash != candidate.candidate_hash:
            raise SelfImprovementEvaluationError("evaluation response candidate hash mismatch")
        if response.metric_name != proposal.metric_name:
            raise SelfImprovementEvaluationError("evaluation response metric does not match proposal")

        expected_baseline = {item.path: item.original_sha256 for item in candidate.files}
        if response.baseline_file_hashes != expected_baseline:
            raise SelfImprovementEvaluationError("evaluation baseline hashes do not match candidate source")

        accepted, reason = self._decision(proposal, response)
        return SelfImprovementEvaluation(
            proposal_id=proposal.id,
            candidate_id=candidate.id,
            candidate_hash=candidate.candidate_hash,
            metric_name=response.metric_name,
            baseline=response.baseline,
            candidate=response.candidate,
            regressions=response.regressions,
            accepted=accepted,
            reason=reason,
        )

    @staticmethod
    def _decision(
        proposal: SelfImprovementProposal,
        response: EvaluationGatewayResponse,
    ) -> tuple[bool, str]:
        if not response.baseline.passed:
            return False, "baseline evaluation is unhealthy; candidate cannot be credited"
        if response.baseline.test_count <= 0:
            return False, "baseline evaluator reported zero tests"
        if not response.candidate.passed:
            return False, "candidate failed the immutable evaluation suite"
        if response.candidate.test_count < response.baseline.test_count:
            return False, "candidate evaluation executed fewer tests than baseline"
        if response.regressions:
            return False, "candidate introduced evaluator-reported regressions"
        improvement = response.candidate.metric_value - response.baseline.metric_value
        if improvement < proposal.minimum_improvement:
            return (
                False,
                f"metric improvement {improvement:.6g} is below required "
                f"{proposal.minimum_improvement:.6g}",
            )
        return (
            True,
            f"candidate passed immutable tests and improved {proposal.metric_name} "
            f"by {improvement:.6g}",
        )
