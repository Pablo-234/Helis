from helis.domain import Opportunity, ScoreDimensions
from helis.scoring import score_opportunity


def opportunity() -> Opportunity:
    return Opportunity(
        title="Test opportunity",
        problem="A sufficiently concrete recurring customer problem.",
        customer="test customer",
        proposed_value="A test value proposition",
    )


def test_higher_execution_risk_reduces_score() -> None:
    low_risk = ScoreDimensions(execution_risk=1)
    high_risk = ScoreDimensions(execution_risk=9)

    assert score_opportunity(opportunity(), low_risk).total > score_opportunity(
        opportunity(), high_risk
    ).total


def test_score_is_bounded() -> None:
    card = score_opportunity(opportunity(), ScoreDimensions())
    assert 0 <= card.total <= 100
