import json

from helis.budget import CycleBudget
from helis.dedup import opportunity_similarity
from helis.domain import (
    BusinessModelHypothesis,
    DeliveryModel,
    Evidence,
    EvidenceKind,
    Observation,
    Opportunity,
    PricingHypothesis,
    RevenueModel,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.money_model import score_business_model, select_diverse_business_models
from helis.scout import OpportunityScout
from helis.store import HelisStore


class StaticProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        return ModelResult(
            content=json.dumps(self.payload),
            prompt_tokens=10,
            completion_tokens=10,
        )


def _model(
    name: str,
    revenue: RevenueModel,
    delivery: DeliveryModel,
    *,
    days: int = 14,
    margin: float = 75,
    owner_minutes: int = 90,
    test_cost: int = 10_000,
    automation_roles: list[str] | None = None,
    human_roles: list[str] | None = None,
) -> BusinessModelHypothesis:
    return BusinessModelHypothesis(
        name=name,
        payer="service business",
        offer=f"{name} for repetitive quoting work",
        value_proposition="Reduce quote turnaround and recurring administrative effort.",
        revenue_model=revenue,
        delivery_model=delivery,
        pricing=PricingHypothesis(
            currency="USD",
            low_cents=5_000,
            high_cents=15_000,
            unit="per month",
        ),
        acquisition_wedge="Directly approach businesses already complaining about quote delays.",
        fulfillment="Deliver the promised quote workflow and measure turnaround improvement.",
        automation_roles=automation_roles or ["prepare quote drafts"],
        human_roles=human_roles or [],
        time_to_first_revenue_days=days,
        gross_margin_pct=margin,
        owner_minutes_per_week_at_scale=owner_minutes,
        test_cost_cents=test_cost,
        primary_risks=["buyers may not pay enough to justify switching"],
    )


def test_money_model_score_rewards_fast_low_effort_automation() -> None:
    efficient = _model(
        "Automated quote agent",
        RevenueModel.SUBSCRIPTION,
        DeliveryModel.AI_AGENT_SERVICE,
        days=3,
        margin=90,
        owner_minutes=20,
        test_cost=1_000,
        automation_roles=["draft quotes", "collect requirements", "follow up"],
    )
    heavy = _model(
        "Manual quote bureau",
        RevenueModel.FIXED_FEE,
        DeliveryModel.MANAGED_SERVICE,
        days=45,
        margin=35,
        owner_minutes=1_200,
        test_cost=50_000,
        automation_roles=[],
        human_roles=["prepare every quote", "manage client communication"],
    )

    assert score_business_model(efficient).total > score_business_model(heavy).total


def test_diversifier_keeps_distinct_economic_shapes() -> None:
    models = [
        _model("Subscription agent A", RevenueModel.SUBSCRIPTION, DeliveryModel.AI_AGENT_SERVICE),
        _model("Subscription agent B", RevenueModel.SUBSCRIPTION, DeliveryModel.AI_AGENT_SERVICE),
        _model("Managed service", RevenueModel.RETAINER, DeliveryModel.MANAGED_SERVICE),
        _model("Lead marketplace", RevenueModel.LEAD_FEE, DeliveryModel.MARKETPLACE),
    ]

    selected = select_diverse_business_models(models, limit=3)

    assert len(selected) == 3
    assert len({(model.revenue_model, model.delivery_model) for model, _ in selected}) == 3


def test_scout_expands_one_problem_into_multiple_money_models_with_one_call() -> None:
    observation = Observation(
        text="Small service firms repeatedly lose hours preparing customer quotes manually.",
        source="fixture",
    )
    payload = {
        "candidates": [
            {
                "title": "Slow manual quoting",
                "problem": "Small service firms spend recurring staff time preparing quotes manually.",
                "customer": "small service firms",
                "proposed_value": "Reduce quote turnaround and administrative effort.",
                "supporting_observation_ids": [str(observation.id)],
                "tags": ["quoting"],
                "money_models": [
                    _model(
                        "Quote agent subscription",
                        RevenueModel.SUBSCRIPTION,
                        DeliveryModel.AI_AGENT_SERVICE,
                    ).model_dump(mode="json"),
                    _model(
                        "Done-for-you quoting",
                        RevenueModel.RETAINER,
                        DeliveryModel.MANAGED_SERVICE,
                    ).model_dump(mode="json"),
                    _model(
                        "Quote workflow license",
                        RevenueModel.LICENSING,
                        DeliveryModel.AUTOMATION,
                    ).model_dump(mode="json"),
                ],
            }
        ]
    }
    provider = StaticProvider(payload)
    budget = CycleBudget(max_model_calls=1, max_tokens=1000)

    opportunities = OpportunityScout(provider, budget).discover([observation])

    assert provider.calls == 1
    assert budget.model_calls == 1
    assert len(opportunities) == 3
    assert len({item.source_problem_id for item in opportunities}) == 1
    assert all(item.source_problem_id is not None for item in opportunities)
    assert all(item.business_model is not None for item in opportunities)
    assert all(item.business_model_score is not None for item in opportunities)
    assert all(item.evidence[0].observation_id == observation.id for item in opportunities)
    assert len({item.id for item in opportunities}) == 3


def test_same_evidence_does_not_merge_different_business_models(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = Observation(text="Teams lose hours preparing quotes manually.", source="fixture")
    evidence = Evidence(
        kind=EvidenceKind.WORKFLOW,
        claim=observation.text,
        source=observation.source,
        observation_id=observation.id,
    )
    first = Opportunity(
        title="Quote agent subscription",
        problem="Teams lose substantial time preparing service quotes manually.",
        customer="service teams",
        proposed_value="Reduce manual quote preparation.",
        evidence=[evidence],
        business_model=_model(
            "Quote agent subscription",
            RevenueModel.SUBSCRIPTION,
            DeliveryModel.AI_AGENT_SERVICE,
        ),
    )
    second = Opportunity(
        title="Managed quoting service",
        problem=first.problem,
        customer=first.customer,
        proposed_value=first.proposed_value,
        evidence=[evidence],
        business_model=_model(
            "Managed quoting service",
            RevenueModel.RETAINER,
            DeliveryModel.MANAGED_SERVICE,
        ),
    )

    engine.ingest(first)
    engine.ingest(second)

    assert opportunity_similarity(first, second) < engine.duplicate_threshold
    assert len(engine.store.list_opportunities()) == 2


def test_same_business_model_and_evidence_still_reinforces_existing_opportunity(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = Observation(text="Teams lose hours preparing quotes manually.", source="fixture")
    evidence = Evidence(
        kind=EvidenceKind.WORKFLOW,
        claim=observation.text,
        source=observation.source,
        observation_id=observation.id,
    )
    model = _model(
        "Quote agent subscription",
        RevenueModel.SUBSCRIPTION,
        DeliveryModel.AI_AGENT_SERVICE,
    )
    first = engine.ingest(
        Opportunity(
            title="Quote agent subscription",
            problem="Teams lose substantial time preparing service quotes manually.",
            customer="service teams",
            proposed_value="Reduce manual quote preparation.",
            evidence=[evidence],
            business_model=model,
        )
    )
    second = engine.ingest(
        Opportunity(
            title="Automated quote subscription",
            problem="Teams lose substantial time preparing service quotes manually.",
            customer="service teams",
            proposed_value="Reduce manual quote preparation.",
            evidence=[evidence],
            business_model=model.model_copy(
                update={"name": "Automated quote subscription"}
            ),
        )
    )

    assert second.id == first.id
    assert len(engine.store.list_opportunities()) == 1
