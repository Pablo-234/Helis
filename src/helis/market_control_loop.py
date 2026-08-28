from __future__ import annotations

from helis.domain import AuditEvent
from helis.engine import HelisEngine
from helis.market_discovery import MarketDiscoveryMachine
from helis.portfolio_scheduler import SchedulerTickReport


class MarketAwarePortfolioControlLoop:
    """Runs the bounded market lane before the existing portfolio control loop."""

    def __init__(self, engine: HelisEngine, market: MarketDiscoveryMachine, portfolio_loop) -> None:
        self.engine = engine
        self.market = market
        self.portfolio_loop = portfolio_loop

    def tick(self, *, max_advances: int) -> SchedulerTickReport:
        try:
            self.market.tick()
        except Exception as exc:  # noqa: BLE001 -- discovery must never suppress funded venture work
            self.engine.store.append_event(
                AuditEvent(
                    event_type="market.discovery_unhandled_failure",
                    data={"error": f"{type(exc).__name__}: {exc}"},
                )
            )
        return self.portfolio_loop.tick(max_advances=max_advances)
