from __future__ import annotations

from uuid import UUID

from helis.engine import HelisEngine
from helis.gtm_channel_experiment import GTMChannelExperimentManager
from helis.gtm_decision import GTMDecision, GTMDecisionEngine
from helis.gtm_experiment import GTMExperimentManager
from helis.gtm_store import GTMStore


class GTMFeedbackRefresher:
    """Keeps deterministic GTM decisions and experiments synchronized with persisted outcomes."""

    def __init__(self, engine: HelisEngine) -> None:
        self.engine = engine
        self.state = GTMStore(engine.store)
        self.decisions = GTMDecisionEngine(engine)
        self.experiments = GTMExperimentManager(engine)
        self.channel_experiments = GTMChannelExperimentManager(engine)

    def refresh(self, opportunity_id: UUID) -> GTMDecision | None:
        if not self.state.list_responses(opportunity_id):
            return None
        decision = self.decisions.evaluate(opportunity_id)
        self.experiments.refresh(opportunity_id)
        self.channel_experiments.refresh(opportunity_id)
        return decision

    def refresh_all(self) -> list[GTMDecision]:
        refreshed: list[GTMDecision] = []
        for opportunity in self.engine.store.list_opportunities():
            decision = self.refresh(opportunity.id)
            if decision is not None:
                refreshed.append(decision)
        return refreshed
