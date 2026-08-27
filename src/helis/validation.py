from __future__ import annotations

from dataclasses import dataclass

from helis.domain import Experiment
from helis.policy import ActionKind, ActionRequest, AutonomyPolicy, PolicyDecision


@dataclass(slots=True)
class ExperimentReview:
    experiment: Experiment
    decisions: list[PolicyDecision]
    priority: float

    @property
    def executable(self) -> bool:
        return all(decision.allowed and not decision.requires_approval for decision in self.decisions)

    @property
    def requires_approval(self) -> bool:
        return any(decision.requires_approval for decision in self.decisions)


def information_priority(experiment: Experiment) -> float:
    """Simple transparent heuristic: favor information, penalize effort and cash."""
    cash_penalty = min(experiment.max_cost_cents / 500, 10)
    denominator = 1 + experiment.effort_score + cash_penalty
    return round(experiment.expected_information_gain * 10 / denominator, 3)


def review_experiment(experiment: Experiment, policy: AutonomyPolicy) -> ExperimentReview:
    requests = [
        ActionRequest(
            kind=ActionKind.RESEARCH,
            description=f"validation experiment: {experiment.title}",
        )
    ]
    if experiment.max_cost_cents > 0:
        requests.append(
            ActionRequest(
                kind=ActionKind.SPEND,
                description=f"experiment budget: {experiment.title}",
                estimated_cost_cents=experiment.max_cost_cents,
            )
        )
    if experiment.requires_external_contact:
        requests.append(
            ActionRequest(
                kind=ActionKind.EXTERNAL_CONTACT,
                description=f"external validation contact: {experiment.title}",
            )
        )
    if experiment.requires_publication:
        requests.append(
            ActionRequest(
                kind=ActionKind.PUBLICATION,
                description=f"publish validation asset: {experiment.title}",
            )
        )

    return ExperimentReview(
        experiment=experiment,
        decisions=[policy.evaluate(request) for request in requests],
        priority=information_priority(experiment),
    )


def rank_experiments(
    experiments: list[Experiment],
    policy: AutonomyPolicy,
) -> list[ExperimentReview]:
    reviews = [review_experiment(experiment, policy) for experiment in experiments]
    return sorted(
        reviews,
        key=lambda review: (
            review.executable,
            review.priority,
            review.experiment.expected_information_gain,
        ),
        reverse=True,
    )
