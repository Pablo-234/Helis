from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from helis.budget import CycleBudget
from helis.contact_gateway import ContactGateway
from helis.engine import HelisEngine
from helis.gtm_discovery import GTMDiscoveryMachine, GTMDiscoveryReport
from helis.gtm_domain import OutreachRunStatus
from helis.gtm_lifecycle import gtm_is_active
from helis.gtm_outreach import GTMContactPolicy, OutreachManager
from helis.gtm_store import GTMStore
from helis.model_provider import ModelProvider
from helis.prospect_gateway import ProspectGateway


@dataclass(slots=True)
class GTMTickReport:
    opportunity_id: UUID
    discovery: GTMDiscoveryReport | None = None
    prepared_run_id: UUID | None = None
    dispatched_run_id: UUID | None = None
    waiting_approval: int = 0
    waiting_result: int = 0
    reason: str = "no_gtm_work"

    @property
    def did_work(self) -> bool:
        if self.prepared_run_id is not None or self.dispatched_run_id is not None:
            return True
        if self.discovery is None:
            return False
        # Reading the same market candidates again is activity, not durable progress.
        return any(
            (
                self.discovery.queries_planned,
                self.discovery.leads_added,
                self.discovery.leads_qualified,
                self.discovery.drafts_created,
            )
        )


class GTMRuntime:
    """Advances one funded venture's GTM state without ever granting contact approval."""

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        *,
        prospect_gateway: ProspectGateway | None = None,
        contact_gateway: ContactGateway | None = None,
        max_waiting_approval: int = 3,
        max_waiting_result: int = 3,
    ) -> None:
        if not 1 <= max_waiting_approval <= 20:
            raise ValueError("max_waiting_approval must be between 1 and 20")
        if not 1 <= max_waiting_result <= 20:
            raise ValueError("max_waiting_result must be between 1 and 20")
        self.engine = engine
        self.budget = budget
        self.state = GTMStore(engine.store)
        self.discovery = GTMDiscoveryMachine(
            engine,
            provider,
            budget,
            prospect_gateway,
            draft_limit=1,
        )
        self.outreach = OutreachManager(
            engine,
            gateway=contact_gateway,
            contact_policy=GTMContactPolicy(),
        )
        self.contact_gateway = contact_gateway
        self.prospect_gateway = prospect_gateway
        self.max_waiting_approval = max_waiting_approval
        self.max_waiting_result = max_waiting_result

    def tick(self, opportunity_id: UUID) -> GTMTickReport:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            raise ValueError(f"venture not found: {opportunity_id}")
        if not gtm_is_active(opportunity.stage):
            return GTMTickReport(
                opportunity_id=opportunity_id,
                reason=f"stage_not_gtm_active:{opportunity.stage.value}",
            )

        runs = self.state.list_outreach_runs(opportunity_id)
        ready = next(
            (
                run
                for run in runs
                if run.status == OutreachRunStatus.READY and run.approval_granted
            ),
            None,
        )
        if ready is not None:
            if self.contact_gateway is None:
                return self._report(opportunity_id, reason="contact_gateway_missing")
            dispatched = self.outreach.dispatch(ready.id)
            return self._report(
                opportunity_id,
                dispatched_run_id=dispatched.id,
                reason="approved_outreach_dispatched",
            )

        counts = self._counts(runs)
        if counts[0] >= self.max_waiting_approval:
            return self._report(opportunity_id, reason="approval_backlog")
        if counts[1] >= self.max_waiting_result:
            return self._report(opportunity_id, reason="result_backlog")

        unprepared = self._unprepared_draft(opportunity_id)
        if unprepared is not None:
            prepared = self.outreach.prepare(unprepared.id)
            return self._report(
                opportunity_id,
                prepared_run_id=prepared.id,
                reason="existing_draft_prepared",
            )

        if self.prospect_gateway is None:
            return self._report(opportunity_id, reason="prospect_gateway_missing")
        if self.budget.model_calls >= self.budget.max_model_calls:
            return self._report(opportunity_id, reason="no_model_capacity")

        discovery = self.discovery.tick(opportunity_id)
        prepared_run_id: UUID | None = None
        if self._counts(self.state.list_outreach_runs(opportunity_id))[0] < self.max_waiting_approval:
            draft = self._unprepared_draft(opportunity_id)
            if draft is not None:
                prepared_run_id = self.outreach.prepare(draft.id).id

        if prepared_run_id is not None:
            reason = "discovery_draft_prepared"
        elif discovery.model_budget_exhausted:
            reason = "no_model_capacity"
        elif self._discovery_did_progress(discovery):
            reason = "discovery_completed"
        else:
            reason = "market_scan_no_new_signal"

        return self._report(
            opportunity_id,
            discovery=discovery,
            prepared_run_id=prepared_run_id,
            reason=reason,
        )

    @staticmethod
    def _discovery_did_progress(discovery: GTMDiscoveryReport) -> bool:
        return any(
            (
                discovery.queries_planned,
                discovery.leads_added,
                discovery.leads_qualified,
                discovery.drafts_created,
            )
        )

    def _unprepared_draft(self, opportunity_id: UUID):
        for draft in self.state.list_drafts(opportunity_id):
            if self.state.get_latest_run_for_draft(draft.id) is None:
                return draft
        return None

    @staticmethod
    def _counts(runs) -> tuple[int, int]:
        waiting_approval = sum(
            run.status == OutreachRunStatus.WAITING_APPROVAL for run in runs
        )
        waiting_result = sum(
            run.status in {OutreachRunStatus.DISPATCHED, OutreachRunStatus.WAITING_RESULT}
            for run in runs
        )
        return waiting_approval, waiting_result

    def _report(
        self,
        opportunity_id: UUID,
        *,
        discovery: GTMDiscoveryReport | None = None,
        prepared_run_id: UUID | None = None,
        dispatched_run_id: UUID | None = None,
        reason: str,
    ) -> GTMTickReport:
        waiting_approval, waiting_result = self._counts(
            self.state.list_outreach_runs(opportunity_id)
        )
        return GTMTickReport(
            opportunity_id=opportunity_id,
            discovery=discovery,
            prepared_run_id=prepared_run_id,
            dispatched_run_id=dispatched_run_id,
            waiting_approval=waiting_approval,
            waiting_result=waiting_result,
            reason=reason,
        )
