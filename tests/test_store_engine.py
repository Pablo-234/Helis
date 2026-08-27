from helis.domain import Opportunity, ScoreDimensions, VentureStage
from helis.engine import HelisEngine
from helis.store import HelisStore


def test_ingest_evaluate_and_rank(tmp_path) -> None:
    helis = HelisEngine(HelisStore(tmp_path / "helis.db"))
    item = Opportunity(
        title="Test opportunity",
        problem="A recurring and expensive manual customer workflow.",
        customer="small businesses",
        proposed_value="Automate a measurable portion of the workflow.",
    )

    helis.ingest(item)
    card = helis.evaluate(item, ScoreDimensions(pain=8, automation_fit=9, execution_risk=2))
    ranked = helis.ranked_queue()

    assert ranked[0].opportunity.stage == VentureStage.EVALUATED
    assert ranked[0].scorecard.total == card.total
    assert len(helis.store.list_events()) == 2
