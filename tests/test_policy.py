from helis.policy import ActionKind, ActionRequest, AutonomyPolicy


def test_research_is_autonomous_by_default() -> None:
    decision = AutonomyPolicy().evaluate(
        ActionRequest(kind=ActionKind.RESEARCH, description="read public market data")
    )
    assert decision.allowed
    assert not decision.requires_approval


def test_spending_is_blocked_by_default() -> None:
    decision = AutonomyPolicy().evaluate(
        ActionRequest(kind=ActionKind.SPEND, description="buy ads", estimated_cost_cents=1)
    )
    assert not decision.allowed
    assert decision.requires_approval


def test_configured_micro_spend_can_be_autonomous() -> None:
    policy = AutonomyPolicy(autonomous_spend_limit_cents=500)
    decision = policy.evaluate(
        ActionRequest(kind=ActionKind.SPEND, description="small experiment", estimated_cost_cents=400)
    )
    assert decision.allowed
