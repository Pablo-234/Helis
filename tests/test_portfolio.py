from __future__ import annotations

from helis.domain import (
    Opportunity,
    Recommendation,
    Scorecard,
    ScoreDimensions,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.portfolio import PortfolioAllocator, PortfolioBudget
from helis.store import HelisStore


def _venture(
    engine: HelisEngine,
    *,
    title: str,
    stage: VentureStage,
    score: float,
    capital_efficiency: float = 7,
    execution_risk: float = 3,
) -> Opportunity:
    opportunity = Opportunity(
        title=title,
        problem=f"{title} addresses a sufficiently concrete recurring customer workflow problem.",
        customer="small service teams",
        proposed_value="reduce time and cost of the workflow",
        stage=stage,
    )
    engine.store.save_opportunity(opportunity)
    engine.store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(
                pain=7,
                frequency=7,
                willingness_to_pay=7,
                market_access=7,
                automation_fit=8,
                speed_to_test=8,
                competition_gap=6,
                evidence_strength=7,
                capital_efficiency=capital_efficiency,
                execution_risk=execution_risk,
            ),
            total=score,
            recommendation=Recommendation.VALIDATE,
            rationale=["test fixture"],
        )
    )
    return opportunity


def test_killed_and_paused_ventures_receive_zero_allocation(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    active = _venture(engine, title="Active venture", stage=VentureStage.VALIDATED, score=72)
    killed = _venture(engine, title="Killed venture", stage=VentureStage.KILLED, score=95)
    paused = _venture(engine, title="Paused venture", stage=VentureStage.PAUSED, score=94)

    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(cash_cents=50_000, model_calls=20)
    )
    allocations = {item.opportunity_id: item for item in plan.allocations}

    assert active.id in allocations
    assert killed.id not in allocations
    assert paused.id not in allocations


def test_scaling_venture_gets_more_than_validated_under_same_budget(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    scaling = _venture(
        engine,
        title="Scaling venture",
        stage=VentureStage.SCALING,
        score=76,
        capital_efficiency=8,
        execution_risk=2,
    )
    validated = _venture(
        engine,
        title="Validated venture",
        stage=VentureStage.VALIDATED,
        score=76,
        capital_efficiency=8,
        execution_risk=2,
    )

    plan = PortfolioAllocator(engine).plan(
        PortfolioBudget(
            cash_cents=100_000,
            model_calls=40,
            reserve_fraction=0.20,
            max_concentration=0.60,
        )
    )
    by_id = {item.opportunity_id: item for item in plan.allocations}

    assert by_id[scaling.id].priority_score > by_id[validated.id].priority_score
    assert by_id[scaling.id].cash_cents > by_id[validated.id].cash_cents
    assert by_id[scaling.id].model_calls >= by_id[validated.id].model_calls


def test_concentration_cap_and_reserve_are_hard_limits(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    venture = _venture(
        engine,
        title="Only eligible venture",
        stage=VentureStage.SCALING,
        score=90,
        capital_efficiency=9,
        execution_risk=1,
    )
    budget = PortfolioBudget(
        cash_cents=50_000,
        model_calls=20,
        reserve_fraction=0.20,
        max_concentration=0.60,
    )

    plan = PortfolioAllocator(engine).plan(budget)
    allocation = next(item for item in plan.allocations if item.opportunity_id == venture.id)

    assert allocation.cash_cents <= int(budget.allocatable_cash_cents * 0.60)
    assert allocation.model_calls <= int(budget.allocatable_model_calls * 0.60)
    assert plan.allocated_cash_cents <= budget.allocatable_cash_cents
    assert plan.allocated_model_calls <= budget.allocatable_model_calls
    assert plan.reserved_cash_cents >= budget.cash_cents - budget.allocatable_cash_cents
    assert plan.reserved_model_calls >= budget.model_calls - budget.allocatable_model_calls


def test_same_portfolio_snapshot_returns_same_plan(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine, title="Venture A", stage=VentureStage.MEASURING, score=70)
    _venture(engine, title="Venture B", stage=VentureStage.VALIDATED, score=68)
    budget = PortfolioBudget(cash_cents=75_000, model_calls=30)
    allocator = PortfolioAllocator(engine)

    first = allocator.plan(budget)
    second = allocator.plan(budget)

    assert first.id == second.id
    assert first.snapshot_hash == second.snapshot_hash
    assert first.allocations == second.allocations


def test_no_eligible_ventures_keeps_everything_reserved(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    _venture(engine, title="Dead venture", stage=VentureStage.KILLED, score=99)
    _venture(engine, title="Paused venture", stage=VentureStage.PAUSED, score=99)
    budget = PortfolioBudget(cash_cents=25_000, model_calls=12)

    plan = PortfolioAllocator(engine).plan(budget)

    assert plan.allocations == []
    assert plan.reserved_cash_cents == budget.cash_cents
    assert plan.reserved_model_calls == budget.model_calls
