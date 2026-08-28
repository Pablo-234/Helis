from helis.engine import HelisEngine
from helis.portfolio_rebalance import PortfolioRebalancer, RebalanceDisposition
from helis.store import HelisStore


def test_rebalancer_without_existing_budget_is_noop(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))

    result = PortfolioRebalancer(engine).rebalance()

    assert result.disposition == RebalanceDisposition.NO_PLAN
    assert result.plan_id is None
