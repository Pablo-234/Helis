from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from helis.budget import BudgetExceeded, CycleBudget
from helis.domain import AuditEvent, Opportunity
from helis.engine import HelisEngine
from helis.gtm_domain import Lead, LeadStage
from helis.gtm_experiment import GTMExperimentManager
from helis.gtm_experiment_domain import GTMExperimentStatus
from helis.gtm_lifecycle import gtm_is_active
from helis.gtm_store import GTMStore
from helis.lead_qualifier import LeadQualifier
from helis.model_provider import ModelProvider
from helis.outreach_drafter import OutreachDrafter
from helis.prospect_gateway import ProspectGateway
from helis.prospect_planner import ProspectPlanner


@dataclass(slots=True)
class GTMDiscoveryReport:
    opportunity_id: UUID | None = None
    queries_planned: int = 0
    candidates_seen: int = 0
    leads_added: int = 0
    leads_qualified: int = 0
    drafts_created: int = 0
    experiment_id: UUID | None = None
    experiment_assignments: int = 0
    experiment_assignment_cap_reached: bool = False
    model_budget_exhausted: bool = False
    gateway_missing: bool = False


class GTMDiscoveryMachine:
    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        budget: CycleBudget,
        gateway: ProspectGateway | None,
        *,
        qualification_threshold: float = 6.5,
        draft_limit: int = 3,
        experiment_manager: GTMExperimentManager | None = None,
    ) -> None:
        self.engine = engine
        self.state = GTMStore(engine.store)
        self.budget = budget
        self.gateway = gateway
        self.planner = ProspectPlanner(provider, budget)
        self.qualifier = LeadQualifier(provider, budget)
        self.drafter = OutreachDrafter(provider, budget)
        self.experiments = experiment_manager
        self.qualification_threshold = qualification_threshold
        self.draft_limit = max(1, min(draft_limit, 5))

    def tick(self, opportunity_id: UUID | None = None) -> GTMDiscoveryReport:
        opportunity = self._target(opportunity_id)
        if opportunity is None:
            return GTMDiscoveryReport()
        report = GTMDiscoveryReport(opportunity_id=opportunity.id)
        if self.gateway is None:
            report.gateway_missing = True
            return report

        validation_results = self.engine.store.list_validation_results(opportunity.id)
        queries = self.state.list_queries(opportunity.id)
        if not queries:
            try:
                queries = self.planner.plan(opportunity, validation_results)
            except BudgetExceeded:
                report.model_budget_exhausted = True
                return report
            for query in queries:
                self.state.save_query(query)
                self._event("gtm.prospect_query_planned", query.id, {"query": query.query})
            report.queries_planned = len(queries)

        fresh: list[Lead] = []
        for query in queries:
            candidates = self.gateway.search(query)
            report.candidates_seen += len(candidates)
            for candidate in candidates:
                lead = Lead(
                    opportunity_id=opportunity.id,
                    organization=candidate.organization,
                    website=candidate.website,
                    contact_endpoint=candidate.contact_endpoint,
                    channel=candidate.channel,
                    evidence=candidate.evidence,
                )
                if self.state.save_lead(lead):
                    fresh.append(lead)
                    report.leads_added += 1
                    self._event(
                        "gtm.lead_discovered",
                        lead.id,
                        {"organization": lead.organization, "evidence_count": len(lead.evidence)},
                    )

        candidates_for_qualification = [
            lead for lead in self.state.list_leads(opportunity.id) if lead.stage == LeadStage.DISCOVERED
        ]
        if candidates_for_qualification:
            try:
                assessments = self.qualifier.qualify(opportunity, candidates_for_qualification)
            except BudgetExceeded:
                report.model_budget_exhausted = True
                return report
            by_id = {item.lead_id: item for item in assessments}
            for lead in candidates_for_qualification:
                assessment = by_id.get(lead.id)
                if assessment is None or assessment.fit_score < self.qualification_threshold:
                    continue
                qualified = lead.model_copy(
                    update={
                        "fit_score": assessment.fit_score,
                        "fit_rationale": assessment.rationale,
                        "stage": LeadStage.QUALIFIED,
                    }
                )
                self.state.update_lead(qualified)
                report.leads_qualified += 1
                self._event(
                    "gtm.lead_qualified",
                    lead.id,
                    {"fit_score": assessment.fit_score},
                )

        draftable = [
            lead
            for lead in self.state.list_leads(opportunity.id)
            if lead.stage == LeadStage.QUALIFIED and self.state.get_draft_for_lead(lead.id) is None
        ][: self.draft_limit]
        if draftable:
            experiment = (
                self.experiments.experiment_for_drafting(opportunity.id)
                if self.experiments is not None
                else None
            )
            assignments = (
                self.experiments.assign_for_leads(opportunity.id, draftable)
                if self.experiments is not None and experiment is not None
                else {}
            )
            if experiment is not None:
                report.experiment_id = experiment.id
                report.experiment_assignments = len(assignments)
            if (
                experiment is not None
                and experiment.status == GTMExperimentStatus.ACTIVE
                and not assignments
            ):
                report.experiment_assignment_cap_reached = True
                return report
            try:
                drafts = self.drafter.draft(
                    opportunity,
                    validation_results,
                    draftable,
                    experiment=experiment if assignments else None,
                    offer_arms=assignments,
                )
            except BudgetExceeded:
                report.model_budget_exhausted = True
                return report
            for draft in drafts:
                self.state.save_draft(draft)
                lead = self.state.get_lead(draft.lead_id)
                if lead is not None:
                    self.state.update_lead(lead.model_copy(update={"stage": LeadStage.DRAFTED}))
                report.drafts_created += 1
                self._event(
                    "gtm.outreach_drafted",
                    draft.id,
                    {
                        "lead_id": str(draft.lead_id),
                        "experiment_id": (
                            str(draft.experiment_id) if draft.experiment_id is not None else None
                        ),
                        "experiment_arm_key": draft.experiment_arm_key,
                    },
                )
        return report

    def _target(self, opportunity_id: UUID | None) -> Opportunity | None:
        if opportunity_id is not None:
            opportunity = self.engine.store.get_opportunity(opportunity_id)
            if opportunity is None or not gtm_is_active(opportunity.stage):
                return None
            return opportunity
        for opportunity in self.engine.store.list_opportunities():
            if gtm_is_active(opportunity.stage):
                return opportunity
        return None

    def _event(self, event_type: str, entity_id: UUID, data: dict) -> None:
        self.engine.store.append_event(
            AuditEvent(event_type=event_type, entity_id=entity_id, data=data)
        )
