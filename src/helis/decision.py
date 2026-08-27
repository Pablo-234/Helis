from __future__ import annotations

from helis.domain import (
    Experiment,
    Opportunity,
    ValidationOutcome,
    ValidationResult,
    VentureDecision,
    VentureDecisionKind,
)


class VentureDecisionEngine:
    """Transparent validation decision rules; no model gets the final vote."""

    def decide(
        self,
        opportunity: Opportunity,
        experiments: list[Experiment],
        results: list[ValidationResult],
    ) -> VentureDecision:
        experiment_types = {item.id: item.experiment_type for item in experiments}
        positives = [item for item in results if item.outcome == ValidationOutcome.POSITIVE]
        negatives = [item for item in results if item.outcome == ValidationOutcome.NEGATIVE]
        positive_weight = sum(item.confidence for item in positives)
        negative_weight = sum(item.confidence for item in negatives)
        independent_positive_types = {
            experiment_types[item.experiment_id]
            for item in positives
            if item.experiment_id in experiment_types
        }
        pivot_results = [item for item in results if item.pivot_signal and item.confidence >= 0.6]

        if any(item.confidence >= 0.88 for item in negatives) or negative_weight >= 1.3:
            confidence = max([item.confidence for item in negatives], default=0.7)
            return VentureDecision(
                opportunity_id=opportunity.id,
                decision=VentureDecisionKind.KILL,
                confidence=confidence,
                result_ids=[item.id for item in results],
                rationale=[
                    f"negative validation weight={negative_weight:.2f}",
                    "strong falsifying evidence crossed the kill threshold",
                ],
            )

        if (
            positive_weight >= 1.4
            and len(independent_positive_types) >= 2
            and negative_weight < 0.5
        ):
            return VentureDecision(
                opportunity_id=opportunity.id,
                decision=VentureDecisionKind.ADVANCE,
                confidence=min(0.95, positive_weight / 2),
                result_ids=[item.id for item in results],
                rationale=[
                    f"positive validation weight={positive_weight:.2f}",
                    f"independent positive experiment types={len(independent_positive_types)}",
                    "evidence is strong enough to hand the venture to the builder phase",
                ],
            )

        if pivot_results and (negative_weight >= 0.5 or positive_weight < 0.8):
            best = max(pivot_results, key=lambda item: item.confidence)
            return VentureDecision(
                opportunity_id=opportunity.id,
                decision=VentureDecisionKind.PIVOT,
                confidence=best.confidence,
                result_ids=[item.id for item in results],
                suggested_pivot=best.pivot_signal,
                rationale=[
                    "validation found a credible adjacent direction while the current hypothesis is weak",
                    f"positive={positive_weight:.2f} negative={negative_weight:.2f}",
                ],
            )

        signal_strength = abs(positive_weight - negative_weight)
        return VentureDecision(
            opportunity_id=opportunity.id,
            decision=VentureDecisionKind.CONTINUE,
            confidence=min(0.75, max(0.25, signal_strength / max(1, len(results)))),
            result_ids=[item.id for item in results],
            rationale=[
                f"positive={positive_weight:.2f} negative={negative_weight:.2f}",
                "current evidence does not justify build, pivot, or kill yet",
            ],
        )
