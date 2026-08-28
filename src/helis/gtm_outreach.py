from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from helis.contact_gateway import ContactGateway
from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.gtm_channel_experiment import GTMChannelExperimentManager
from helis.gtm_domain import (
    Lead,
    LeadResponse,
    LeadResponseKind,
    LeadStage,
    OutreachDraft,
    OutreachRun,
    OutreachRunStatus,
    RevenueEvent,
    lead_contact_options,
)
from helis.gtm_experiment import GTMExperimentManager
from helis.gtm_feedback import GTMFeedbackRefresher
from helis.gtm_lifecycle import gtm_is_active
from helis.gtm_store import GTMStore, lead_identity
from helis.policy import ActionKind, ActionRequest, AutonomyPolicy


class OutreachError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GTMContactPolicy:
    max_contacts_per_day: int = 3
    max_contacts_per_identity: int = 1
    require_run_approval: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_contacts_per_day <= 50:
            raise ValueError("max_contacts_per_day must be between 1 and 50")
        if not 1 <= self.max_contacts_per_identity <= 3:
            raise ValueError("max_contacts_per_identity must be between 1 and 3")


def draft_hash(draft: OutreachDraft) -> str:
    payload: dict[str, object] = {
        "id": str(draft.id),
        "lead_id": str(draft.lead_id),
        "opportunity_id": str(draft.opportunity_id),
        "channel": draft.channel.value,
        "subject": draft.subject,
        "body": draft.body,
        "evidence_ids": [str(item) for item in draft.evidence_ids],
    }
    # Preserve historical hashes when newer optional bindings are absent.
    if draft.experiment_id is not None:
        payload["experiment_id"] = str(draft.experiment_id)
        payload["experiment_arm_key"] = draft.experiment_arm_key
    if draft.contact_endpoint is not None:
        payload["contact_endpoint"] = draft.contact_endpoint
    if draft.channel_experiment_id is not None:
        payload["channel_experiment_id"] = str(draft.channel_experiment_id)
        payload["channel_experiment_arm_key"] = draft.channel_experiment_arm_key
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OutreachManager:
    def __init__(
        self,
        engine: HelisEngine,
        *,
        gateway: ContactGateway | None = None,
        contact_policy: GTMContactPolicy | None = None,
        autonomy_policy: AutonomyPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.state = GTMStore(engine.store)
        self.feedback = GTMFeedbackRefresher(engine)
        self.experiments = GTMExperimentManager(engine)
        self.channel_experiments = GTMChannelExperimentManager(engine)
        self.gateway = gateway
        self.contact_policy = contact_policy or GTMContactPolicy()
        self.autonomy_policy = autonomy_policy or AutonomyPolicy()

    def prepare(self, draft_id: UUID) -> OutreachRun:
        draft = self._require_draft(draft_id)
        lead = self._require_lead(draft.lead_id)
        existing = self.state.get_latest_run_for_draft(draft.id)
        if existing is not None:
            return existing
        self._validate_venture_for_contact(lead.opportunity_id)
        self._validate_lead_for_contact(lead)
        self._validate_draft_contact_binding(draft, lead)

        decision = self.autonomy_policy.evaluate(
            ActionRequest(
                kind=ActionKind.EXTERNAL_CONTACT,
                description=f"first B2B contact to {lead.organization}",
            )
        )
        autonomous = (
            decision.allowed
            and not decision.requires_approval
            and not self.contact_policy.require_run_approval
        )
        run = OutreachRun(
            draft_id=draft.id,
            lead_id=lead.id,
            opportunity_id=lead.opportunity_id,
            draft_hash=draft_hash(draft),
            status=(OutreachRunStatus.READY if autonomous else OutreachRunStatus.WAITING_APPROVAL),
            approval_granted=autonomous,
        )
        self._save_run(run, "gtm.outreach_prepared")
        return run

    def approve(self, run_id: UUID) -> OutreachRun:
        run = self._require_run(run_id)
        if run.status in {
            OutreachRunStatus.DISPATCHED,
            OutreachRunStatus.WAITING_RESULT,
            OutreachRunStatus.COMPLETED,
        }:
            return run
        if run.status != OutreachRunStatus.WAITING_APPROVAL:
            raise OutreachError(f"run {run.id} cannot be approved from {run.status.value}")
        approved = run.model_copy(
            update={
                "status": OutreachRunStatus.READY,
                "approval_granted": True,
                "updated_at": utc_now(),
            }
        )
        self._save_run(approved, "gtm.outreach_approved")
        return approved

    def dispatch(self, run_id: UUID, *, now: datetime | None = None) -> OutreachRun:
        run = self._require_run(run_id)
        if run.status in {
            OutreachRunStatus.DISPATCHED,
            OutreachRunStatus.WAITING_RESULT,
            OutreachRunStatus.COMPLETED,
        } and run.external_ref:
            return run
        if run.status != OutreachRunStatus.READY or not run.approval_granted:
            raise OutreachError("outreach requires a ready approved run")
        if self.gateway is None:
            raise OutreachError("contact gateway is not configured")

        opportunity = self.engine.store.get_opportunity(run.opportunity_id)
        if opportunity is None:
            return self._block(run, "venture no longer exists")
        if not gtm_is_active(opportunity.stage):
            return self._block(run, f"venture stage {opportunity.stage.value} is not GTM-active")

        draft = self._require_draft(run.draft_id)
        lead = self._require_lead(run.lead_id)
        self._validate_lead_for_contact(lead)
        if draft_hash(draft) != run.draft_hash:
            return self._block(run, "draft changed after approval")
        try:
            self._validate_draft_contact_binding(draft, lead)
        except OutreachError as exc:
            return self._block(run, str(exc))
        self._enforce_contact_limits(lead, now=now)

        try:
            ack = self.gateway.send(run, lead, draft)
        except Exception as exc:
            failed = run.model_copy(
                update={
                    "status": OutreachRunStatus.FAILED,
                    "error": f"{type(exc).__name__}: {exc}",
                    "updated_at": utc_now(),
                }
            )
            self._save_run(failed, "gtm.outreach_failed")
            raise OutreachError(failed.error) from exc

        sent_at = now or datetime.now(UTC)
        dispatched = run.model_copy(
            update={
                "status": OutreachRunStatus.WAITING_RESULT,
                "external_ref": ack.dispatch_id,
                "destination": self.gateway.safe_destination,
                "dispatched_at": sent_at,
                "updated_at": sent_at,
            }
        )
        self._save_run(dispatched, "gtm.outreach_dispatched")
        self.state.update_lead(lead.model_copy(update={"stage": LeadStage.CONTACTED}))
        return dispatched

    def record_response(self, response: LeadResponse) -> tuple[LeadResponse, RevenueEvent | None]:
        run = self._require_run(response.run_id)
        existing = self.state.get_response_for_run(run.id)
        if existing is not None:
            self._refresh_feedback(existing.opportunity_id, existing.id)
            self._refresh_experiment(existing.opportunity_id, existing.id)
            self._refresh_channel_experiment(existing.opportunity_id, existing.id)
            return existing, self.state.get_revenue_for_response(existing.id)
        if run.status not in {OutreachRunStatus.DISPATCHED, OutreachRunStatus.WAITING_RESULT}:
            raise OutreachError(f"run {run.id} is not waiting for a response")
        if response.lead_id != run.lead_id or response.opportunity_id != run.opportunity_id:
            raise OutreachError("response does not match outreach run scope")
        if response.revenue_cents > 0 and response.kind != LeadResponseKind.SALE:
            raise OutreachError("revenue may only be attributed to a SALE response")

        lead = self._require_lead(run.lead_id)
        draft = self._require_draft(run.draft_id)
        self.state.save_response(response)
        revenue: RevenueEvent | None = None
        if response.revenue_cents > 0:
            revenue = RevenueEvent(
                opportunity_id=response.opportunity_id,
                lead_id=response.lead_id,
                response_id=response.id,
                amount_cents=response.revenue_cents,
                currency=response.currency.upper(),
                source="gtm_outreach",
                external_ref=run.external_ref,
            )
            self.state.save_revenue(revenue)

        lead_stage = self._response_stage(response.kind)
        if response.kind in {LeadResponseKind.NOT_INTERESTED, LeadResponseKind.BOUNCE}:
            identity = lead_identity(lead)
            self.state.suppress(identity, response.kind.value)
            lead_stage = LeadStage.SUPPRESSED
        self.state.update_lead(lead.model_copy(update={"stage": lead_stage}))

        completed = run.model_copy(
            update={
                "status": OutreachRunStatus.COMPLETED,
                "completed_at": response.created_at,
                "updated_at": response.created_at,
            }
        )
        self._save_run(completed, "gtm.outreach_completed")
        self.engine.store.append_event(
            AuditEvent(
                event_type="gtm.response_recorded",
                entity_id=response.id,
                data={
                    "run_id": str(run.id),
                    "lead_id": str(lead.id),
                    "kind": response.kind.value,
                    "revenue_cents": response.revenue_cents,
                    "currency": response.currency.upper(),
                    "experiment_id": str(draft.experiment_id) if draft.experiment_id else None,
                    "experiment_arm_key": draft.experiment_arm_key,
                    "channel_experiment_id": (
                        str(draft.channel_experiment_id)
                        if draft.channel_experiment_id is not None
                        else None
                    ),
                    "channel_experiment_arm_key": draft.channel_experiment_arm_key,
                    "channel": draft.channel.value,
                },
            )
        )
        if revenue is not None:
            self.engine.store.append_event(
                AuditEvent(
                    event_type="gtm.revenue_attributed",
                    entity_id=revenue.id,
                    data={
                        "opportunity_id": str(revenue.opportunity_id),
                        "lead_id": str(revenue.lead_id),
                        "amount_cents": revenue.amount_cents,
                        "currency": revenue.currency,
                    },
                )
            )
        self._refresh_feedback(response.opportunity_id, response.id)
        self._refresh_experiment(response.opportunity_id, response.id)
        self._refresh_channel_experiment(response.opportunity_id, response.id)
        return response, revenue

    def _refresh_feedback(self, opportunity_id: UUID, response_id: UUID) -> None:
        try:
            self.feedback.refresh(opportunity_id)
        except Exception as exc:  # noqa: BLE001 -- persisted market outcome must remain accepted
            self.engine.store.append_event(
                AuditEvent(
                    event_type="gtm.feedback_refresh_failed",
                    entity_id=response_id,
                    data={
                        "opportunity_id": str(opportunity_id),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            )

    def _refresh_experiment(self, opportunity_id: UUID, response_id: UUID) -> None:
        try:
            self.experiments.refresh(opportunity_id)
        except Exception as exc:  # noqa: BLE001 -- persisted response must remain accepted
            self.engine.store.append_event(
                AuditEvent(
                    event_type="gtm.experiment_refresh_failed",
                    entity_id=response_id,
                    data={
                        "opportunity_id": str(opportunity_id),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            )

    def _refresh_channel_experiment(self, opportunity_id: UUID, response_id: UUID) -> None:
        try:
            self.channel_experiments.refresh(opportunity_id)
        except Exception as exc:  # noqa: BLE001 -- persisted response must remain accepted
            self.engine.store.append_event(
                AuditEvent(
                    event_type="gtm.channel_experiment_refresh_failed",
                    entity_id=response_id,
                    data={
                        "opportunity_id": str(opportunity_id),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            )

    def _enforce_contact_limits(self, lead: Lead, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        identity = lead_identity(lead)
        daily = 0
        identity_contacts = 0
        for existing in self.state.list_outreach_runs():
            if existing.dispatched_at is None:
                continue
            if existing.dispatched_at.astimezone(UTC).date() == current.astimezone(UTC).date():
                daily += 1
            existing_lead = self.state.get_lead(existing.lead_id)
            if existing_lead is not None and lead_identity(existing_lead) == identity:
                identity_contacts += 1
        if daily >= self.contact_policy.max_contacts_per_day:
            raise OutreachError("daily contact cap reached")
        if identity_contacts >= self.contact_policy.max_contacts_per_identity:
            raise OutreachError("identity contact cap reached")

    def _validate_venture_for_contact(self, opportunity_id: UUID) -> None:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            raise OutreachError("venture no longer exists")
        if not gtm_is_active(opportunity.stage):
            raise OutreachError(f"venture stage {opportunity.stage.value} is not GTM-active")

    def _validate_lead_for_contact(self, lead: Lead) -> None:
        if lead.stage == LeadStage.SUPPRESSED or self.state.is_suppressed(lead_identity(lead)):
            raise OutreachError("lead is suppressed")
        if not lead_contact_options(lead):
            raise OutreachError("lead has no public contact endpoint")
        if lead.stage not in {LeadStage.DRAFTED, LeadStage.QUALIFIED, LeadStage.CONTACTED}:
            raise OutreachError(f"lead stage {lead.stage.value} is not contactable")

    @staticmethod
    def _validate_draft_contact_binding(draft: OutreachDraft, lead: Lead) -> None:
        if draft.contact_endpoint is None:
            return
        valid = any(
            option.channel == draft.channel and option.endpoint == draft.contact_endpoint
            for option in lead_contact_options(lead)
        )
        if not valid:
            raise OutreachError("draft contact endpoint is not a stored public endpoint for this lead")

    def _response_stage(self, kind: LeadResponseKind) -> LeadStage:
        if kind == LeadResponseKind.SALE:
            return LeadStage.WON
        if kind in {LeadResponseKind.INTERESTED, LeadResponseKind.MEETING}:
            return LeadStage.REPLIED
        return LeadStage.LOST

    def _block(self, run: OutreachRun, reason: str) -> OutreachRun:
        blocked = run.model_copy(
            update={
                "status": OutreachRunStatus.BLOCKED,
                "error": reason,
                "updated_at": utc_now(),
            }
        )
        self._save_run(blocked, "gtm.outreach_blocked")
        raise OutreachError(reason)

    def _require_draft(self, draft_id: UUID) -> OutreachDraft:
        draft = self.state.get_draft(draft_id)
        if draft is None:
            raise OutreachError(f"outreach draft not found: {draft_id}")
        return draft

    def _require_lead(self, lead_id: UUID) -> Lead:
        lead = self.state.get_lead(lead_id)
        if lead is None:
            raise OutreachError(f"lead not found: {lead_id}")
        return lead

    def _require_run(self, run_id: UUID) -> OutreachRun:
        run = self.state.get_outreach_run(run_id)
        if run is None:
            raise OutreachError(f"outreach run not found: {run_id}")
        return run

    def _save_run(self, run: OutreachRun, event_type: str) -> None:
        self.state.save_outreach_run(run)
        self.engine.store.append_event(
            AuditEvent(
                event_type=event_type,
                entity_id=run.id,
                data={
                    "draft_id": str(run.draft_id),
                    "lead_id": str(run.lead_id),
                    "opportunity_id": str(run.opportunity_id),
                    "status": run.status.value,
                    "approval_granted": run.approval_granted,
                    "draft_hash": run.draft_hash,
                    "external_ref": run.external_ref,
                    "error": run.error,
                },
            )
        )
