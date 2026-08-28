from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from helis.domain import BusinessModelHypothesis, Opportunity


@dataclass(frozen=True, slots=True)
class BusinessModelScore:
    total: float
    speed_to_revenue: float
    gross_margin: float
    low_owner_effort: float
    automation_leverage: float
    test_capital_efficiency: float


def _clamp(value: float, low: float = 0.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def score_business_model(model: BusinessModelHypothesis) -> BusinessModelScore:
    """Cheap pre-validation heuristic. Inputs are hypotheses and never treated as evidence."""

    speed = _clamp(10.0 - (model.time_to_first_revenue_days - 1) / 9.0)
    margin = _clamp(model.gross_margin_pct / 10.0)
    owner_effort = _clamp(10.0 - model.owner_minutes_per_week_at_scale / 120.0)
    role_count = len(model.automation_roles) + len(model.human_roles)
    automation_share = len(model.automation_roles) / role_count if role_count else 0.0
    automation = _clamp(automation_share * 10.0)
    capital = _clamp(10.0 / (1.0 + model.test_cost_cents / 25_000.0))

    total = round(
        speed * 2.5
        + margin * 2.0
        + owner_effort * 2.0
        + automation * 1.5
        + capital * 2.0,
        2,
    )
    return BusinessModelScore(
        total=total,
        speed_to_revenue=round(speed, 2),
        gross_margin=round(margin, 2),
        low_owner_effort=round(owner_effort, 2),
        automation_leverage=round(automation, 2),
        test_capital_efficiency=round(capital, 2),
    )


def select_diverse_business_models(
    models: list[BusinessModelHypothesis],
    *,
    limit: int = 3,
) -> list[tuple[BusinessModelHypothesis, BusinessModelScore]]:
    if limit < 1:
        raise ValueError("business model selection limit must be positive")

    scored = [(model, score_business_model(model)) for model in models]
    scored.sort(key=lambda item: (-item[1].total, item[0].name.casefold()))

    selected: list[tuple[BusinessModelHypothesis, BusinessModelScore]] = []
    seen_shapes: set[tuple[str, str]] = set()
    seen_offers: set[str] = set()
    for model, score in scored:
        shape = (model.revenue_model.value, model.delivery_model.value)
        normalized_offer = " ".join(model.offer.casefold().split())
        if shape in seen_shapes or normalized_offer in seen_offers:
            continue
        selected.append((model, score))
        seen_shapes.add(shape)
        seen_offers.add(normalized_offer)
        if len(selected) >= limit:
            break
    return selected


def expand_problem_opportunity(
    problem: Opportunity,
    models: list[BusinessModelHypothesis],
    *,
    limit: int = 3,
) -> list[Opportunity]:
    expanded: list[Opportunity] = []
    for model, score in select_diverse_business_models(models, limit=limit):
        tags = list(problem.tags)
        for tag in (
            f"revenue:{model.revenue_model.value}",
            f"delivery:{model.delivery_model.value}",
        ):
            if tag not in tags:
                tags.append(tag)
        expanded.append(
            problem.model_copy(
                update={
                    "id": uuid4(),
                    "title": model.name,
                    "proposed_value": model.value_proposition,
                    "source_problem_id": problem.id,
                    "business_model": model,
                    "business_model_score": score.total,
                    "tags": tags,
                }
            )
        )
    return expanded
