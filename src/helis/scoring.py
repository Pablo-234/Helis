from __future__ import annotations

from helis.domain import (
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
)

WEIGHTS: dict[str, float] = {
    "pain": 0.15,
    "frequency": 0.10,
    "willingness_to_pay": 0.15,
    "market_access": 0.10,
    "automation_fit": 0.10,
    "speed_to_test": 0.10,
    "competition_gap": 0.10,
    "evidence_strength": 0.10,
    "capital_efficiency": 0.05,
    "execution_risk": 0.05,
}


def score_opportunity(
    opportunity: Opportunity,
    dimensions: ScoreDimensions,
) -> Scorecard:
    values = dimensions.model_dump()
    adjusted = dict(values)
    adjusted["execution_risk"] = 10 - values["execution_risk"]

    total = round(sum(adjusted[key] * weight for key, weight in WEIGHTS.items()) * 10, 2)

    if total >= 75:
        recommendation = Recommendation.VALIDATE
    elif total >= 58:
        recommendation = Recommendation.EXPLORE
    else:
        recommendation = Recommendation.KILL

    rationale: list[str] = []
    strongest = max(
        (key for key in values if key != "execution_risk"),
        key=lambda key: values[key],
    )
    weakest = min(
        (key for key in values if key != "execution_risk"),
        key=lambda key: values[key],
    )
    rationale.append(f"strongest_dimension={strongest}:{values[strongest]:.1f}")
    rationale.append(f"weakest_dimension={weakest}:{values[weakest]:.1f}")
    rationale.append(f"execution_risk={values['execution_risk']:.1f}/10")
    rationale.append(f"evidence_items={len(opportunity.evidence)}")

    return Scorecard(
        opportunity_id=opportunity.id,
        dimensions=dimensions,
        total=total,
        recommendation=recommendation,
        rationale=rationale,
    )
