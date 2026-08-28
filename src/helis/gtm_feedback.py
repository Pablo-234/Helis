from __future__ import annotations

from uuid import UUID

from helis.gtm_decision import GTMDecision, GTMDecisionEngine
from helis.gtm_store import GTMStore


class GTMFeedbackRefresher:
    """Keeps deterministic GTM decisions synchronized with persisted market outcomes."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.state = GTMStore(engine.store)
        self.decisions = GTMDecisionEngine(engine)

    def refresh(self, opportunity_id: UUID) -> GTMDecision | None:
        if not self.state.list_responses(opportunity_id):
            return None
        return self.decisions.evaluate(opportunity_id)

    def refresh_all(self) -> list[GTMDecision]:
        refreshed: list[GTMDecision] = []
        for opportunity in self.engine.store.list_opportunities():
            decision = self.refresh(opportunity.id)
            if decision is not None:
                refreshed.append(decision)
        return refreshed
