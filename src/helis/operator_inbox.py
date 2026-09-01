from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from helis.child_agent_orchestration_domain import (
    OrchestrationStatus,
    OrchestrationStepStatus,
)
from helis.child_agent_orchestration_store import ChildAgentOrchestrationStore
from helis.commerce_domain import CheckoutRunStatus
from helis.commerce_manager import CommerceManager
from helis.commerce_store import CommerceStore
from helis.domain import AuditEvent, ExperimentRunStatus, utc_now
from helis.engine import HelisEngine
from helis.gtm_domain import OutreachRunStatus
from helis.gtm_outreach import OutreachManager
from helis.gtm_store import GTMStore
from helis.operator_domain import (
    OperatorDecision,
    OperatorDecisionReceipt,
    OperatorInboxItem,
    OperatorRequestKind,
    OperatorRequestType,
)
from helis.policy import AutonomyPolicy
from helis.preview_domain import PreviewPublishStatus
from helis.preview_publisher import PreviewPublisher
from helis.preview_store import PreviewPublicationStore
from helis.self_improvement_branch_domain import BranchMaterializationStatus
from helis.self_improvement_branch_manager import SelfImprovementBranchManager
from helis.self_improvement_branch_store import SelfImprovementBranchStore
from helis.self_improvement_merge_domain import SelfImprovementMergeStatus
from helis.self_improvement_merge_manager import SelfImprovementMergeManager
from helis.self_improvement_merge_store import SelfImprovementMergeStore
from helis.validation_execution import ValidationRunner
from helis.venture_architecture_domain import CapabilityImplementation


class OperatorInboxError(RuntimeError):
    pass


class OperatorInbox:
    """Read-only approval aggregation plus explicit hash-confirmed operator decisions."""

    def __init__(
        self,
        engine: HelisEngine,
        *,
        workspace_root: str | Path = ".helis/workspaces",
        self_improvement_root: str | Path = ".helis/self-improvement",
    ) -> None:
        self.engine = engine
        self.workspace_root = Path(workspace_root)
        self.self_improvement_root = Path(self_improvement_root)
        self.previews = PreviewPublicationStore(engine.store)
        self.commerce = CommerceStore(engine.store)
        self.gtm = GTMStore(engine.store)
        self.self_branches = SelfImprovementBranchStore(engine.store)
        self.self_merges = SelfImprovementMergeStore(engine.store)
        self.orchestrations = ChildAgentOrchestrationStore(engine.store)

    def list_items(self) -> list[OperatorInboxItem]:
        items = [
            *self._validation_items(),
            *self._preview_items(),
            *self._commerce_items(),
            *self._outreach_items(),
            *self._self_branch_items(),
            *self._self_merge_items(),
            *self._capability_result_items(),
        ]
        return sorted(items, key=lambda item: (item.priority, item.updated_at), reverse=True)

    def get(self, key: str) -> OperatorInboxItem | None:
        return next((item for item in self.list_items() if item.key == key), None)

    def approve(self, key: str, *, confirmation_token: str) -> OperatorDecisionReceipt:
        item = self._require_confirmed_approval(key, confirmation_token)
        if item.kind == OperatorRequestKind.VALIDATION:
            updated = ValidationRunner(self.engine, AutonomyPolicy()).approve(item.run_id)
        elif item.kind == OperatorRequestKind.PREVIEW_PUBLICATION:
            updated = PreviewPublisher(
                self.engine,
                workspace_root=self.workspace_root,
            ).approve(item.run_id)
        elif item.kind == OperatorRequestKind.COMMERCE_CHECKOUT:
            updated = CommerceManager(self.engine).approve(item.run_id)
        elif item.kind == OperatorRequestKind.OUTREACH:
            updated = OutreachManager(self.engine).approve(item.run_id)
        elif item.kind == OperatorRequestKind.SELF_BRANCH:
            updated = SelfImprovementBranchManager(
                self.engine,
                sandbox_root=str(self.self_improvement_root),
            ).approve(item.run_id)
        elif item.kind == OperatorRequestKind.SELF_MERGE:
            updated = SelfImprovementMergeManager(
                self.engine,
                sandbox_root=str(self.self_improvement_root),
            ).approve(item.run_id)
        else:
            raise OperatorInboxError("input requests cannot be approved")
        receipt = OperatorDecisionReceipt(
            key=item.key,
            decision=OperatorDecision.APPROVE,
            run_id=item.run_id,
            kind=item.kind,
            confirmation_token=confirmation_token,
            resulting_status=updated.status.value,
        )
        self._decision_event(item, receipt)
        return receipt

    def reject(
        self,
        key: str,
        *,
        confirmation_token: str,
        reason: str,
    ) -> OperatorDecisionReceipt:
        item = self._require_confirmed_approval(key, confirmation_token)
        normalized_reason = reason.strip()
        if len(normalized_reason) < 3 or len(normalized_reason) > 1000:
            raise ValueError("rejection reason must contain between 3 and 1000 characters")
        resulting_status = self._cancel(item, normalized_reason)
        receipt = OperatorDecisionReceipt(
            key=item.key,
            decision=OperatorDecision.REJECT,
            run_id=item.run_id,
            kind=item.kind,
            confirmation_token=confirmation_token,
            resulting_status=resulting_status,
            reason=normalized_reason,
        )
        self._decision_event(item, receipt)
        return receipt

    def _validation_items(self) -> list[OperatorInboxItem]:
        items: list[OperatorInboxItem] = []
        for run in self.engine.store.list_experiment_runs():
            if run.status != ExperimentRunStatus.WAITING_APPROVAL or run.approval_granted:
                continue
            experiment = self.engine.store.get_experiment(run.experiment_id)
            if experiment is None:
                continue
            details = {
                "experiment_type": experiment.experiment_type.value,
                "max_cost_cents": str(experiment.max_cost_cents),
                "max_duration_hours": str(experiment.max_duration_hours),
                "requires_external_contact": str(experiment.requires_external_contact).lower(),
                "requires_publication": str(experiment.requires_publication).lower(),
                "adapter": run.adapter or "unselected",
            }
            items.append(
                self._approval_item(
                    kind=OperatorRequestKind.VALIDATION,
                    run_id=run.id,
                    opportunity_id=run.opportunity_id,
                    title=experiment.title,
                    summary=experiment.hypothesis,
                    consequence=(
                        "Authorizes exactly this validation run; external dispatch may contact "
                        f"people/publish and consume up to {experiment.max_cost_cents} cents."
                    ),
                    binding=str(experiment.id),
                    details=details,
                    priority=60,
                    updated_at=run.updated_at,
                )
            )
        return items

    def _preview_items(self) -> list[OperatorInboxItem]:
        return [
            self._approval_item(
                kind=OperatorRequestKind.PREVIEW_PUBLICATION,
                run_id=run.id,
                opportunity_id=run.opportunity_id,
                title="Publish reviewed venture preview",
                summary=f"Exact reviewed artifact {run.artifact_hash[:16]}… is ready to publish.",
                consequence="Publishes only the hash-locked reviewed artifact to the configured destination.",
                binding=run.artifact_hash,
                details={"artifact_hash": run.artifact_hash, "destination": run.destination or "configured adapter"},
                priority=75,
                updated_at=run.updated_at,
            )
            for run in self.previews.list_runs()
            if run.status == PreviewPublishStatus.WAITING_APPROVAL and not run.approval_granted
        ]

    def _commerce_items(self) -> list[OperatorInboxItem]:
        items: list[OperatorInboxItem] = []
        for run in self.commerce.list_runs():
            if run.status != CheckoutRunStatus.WAITING_APPROVAL or run.approval_granted:
                continue
            offer = self.commerce.get_offer(run.offer_id)
            if offer is None:
                continue
            items.append(
                self._approval_item(
                    kind=OperatorRequestKind.COMMERCE_CHECKOUT,
                    run_id=run.id,
                    opportunity_id=run.opportunity_id,
                    title=f"Create checkout: {offer.name}",
                    summary=f"{offer.display_price}, {offer.billing_mode.value}; {offer.description}",
                    consequence=(
                        "Creates a customer-facing payment link for the exact immutable offer and price."
                    ),
                    binding=run.offer_hash,
                    details={
                        "offer_hash": run.offer_hash,
                        "price": offer.display_price,
                        "billing_mode": offer.billing_mode.value,
                    },
                    priority=85,
                    updated_at=run.updated_at,
                )
            )
        return items

    def _outreach_items(self) -> list[OperatorInboxItem]:
        items: list[OperatorInboxItem] = []
        for run in self.gtm.list_outreach_runs():
            if run.status != OutreachRunStatus.WAITING_APPROVAL or run.approval_granted:
                continue
            draft = self.gtm.get_draft(run.draft_id)
            lead = self.gtm.get_lead(run.lead_id)
            if draft is None or lead is None:
                continue
            endpoint = draft.contact_endpoint or lead.contact_endpoint or "missing"
            details = {
                "organization": lead.organization,
                "channel": draft.channel.value,
                "endpoint": endpoint,
                "subject": draft.subject or "-",
                "draft_hash": run.draft_hash,
                "message": draft.body,
            }
            items.append(
                self._approval_item(
                    kind=OperatorRequestKind.OUTREACH,
                    run_id=run.id,
                    opportunity_id=run.opportunity_id,
                    title=f"First contact: {lead.organization}",
                    summary=f"{draft.channel.value} to {endpoint}: {draft.body[:1200]}",
                    consequence=(
                        "Authorizes one first-contact dispatch to the exact public endpoint and exact draft."
                    ),
                    binding=run.draft_hash,
                    details=details,
                    priority=90,
                    updated_at=run.updated_at,
                )
            )
        return items

    def _self_branch_items(self) -> list[OperatorInboxItem]:
        return [
            self._approval_item(
                kind=OperatorRequestKind.SELF_BRANCH,
                run_id=run.id,
                opportunity_id=None,
                title=f"Create self-improvement review branch {run.branch_name}",
                summary=(
                    f"Candidate {run.candidate_hash[:16]}… on base {run.base_revision[:16]}…."
                ),
                consequence="Allows only exact review-branch materialization; it does not merge code.",
                binding=run.candidate_hash,
                details={
                    "candidate_hash": run.candidate_hash,
                    "base_revision": run.base_revision,
                    "branch_name": run.branch_name,
                },
                priority=70,
                updated_at=run.updated_at,
            )
            for run in self.self_branches.list()
            if run.status == BranchMaterializationStatus.WAITING_APPROVAL
            and not run.approval_granted
        ]

    def _self_merge_items(self) -> list[OperatorInboxItem]:
        items: list[OperatorInboxItem] = []
        for run in self.self_merges.list():
            if run.status != SelfImprovementMergeStatus.WAITING_APPROVAL or run.approval_granted:
                continue
            head = run.ci_attestation.head_revision if run.ci_attestation else "missing"
            items.append(
                self._approval_item(
                    kind=OperatorRequestKind.SELF_MERGE,
                    run_id=run.id,
                    opportunity_id=None,
                    title=f"Merge self-improvement branch {run.branch_name}",
                    summary=f"Candidate {run.candidate_hash[:16]}…; CI head {head[:16]}….",
                    consequence=(
                        "Grants the second approval required before fresh CI re-attestation and merge."
                    ),
                    binding=run.ci_attestation_hash or run.candidate_hash,
                    details={
                        "candidate_hash": run.candidate_hash,
                        "base_revision": run.base_revision,
                        "head_revision": head,
                        "ci_attestation_hash": run.ci_attestation_hash or "missing",
                    },
                    priority=100,
                    updated_at=run.updated_at,
                )
            )
        return items

    def _capability_result_items(self) -> list[OperatorInboxItem]:
        items: list[OperatorInboxItem] = []
        for run in self.orchestrations.list_all():
            if run.status not in {OrchestrationStatus.PENDING, OrchestrationStatus.BLOCKED}:
                continue
            completed = {
                step.capability_key
                for step in run.steps
                if step.status == OrchestrationStepStatus.COMPLETED
            }
            for step in run.steps:
                if (
                    step.status != OrchestrationStepStatus.PENDING
                    or step.implementation == CapabilityImplementation.AI_AGENT
                    or not set(step.depends_on) <= completed
                ):
                    continue
                key = f"{OperatorRequestKind.CAPABILITY_RESULT.value}:{run.id}:{step.capability_key}"
                items.append(
                    OperatorInboxItem(
                        key=key,
                        kind=OperatorRequestKind.CAPABILITY_RESULT,
                        request_type=OperatorRequestType.INPUT,
                        run_id=run.id,
                        opportunity_id=run.opportunity_id,
                        capability_key=step.capability_key,
                        venture_title=self._venture_title(run.opportunity_id),
                        title=f"Supply result: {step.capability_key}",
                        summary=(
                            f"The {step.implementation.value} capability is ready and blocks downstream work."
                        ),
                        consequence=(
                            "Records an observed venture-local result; it does not authorize an external action."
                        ),
                        binding=f"{run.architecture_id}:{step.capability_key}",
                        action_command=(
                            f"helis-agent supply-capability-result {run.id} {step.capability_key} "
                            '--output "<observed result>"'
                        ),
                        details={
                            "implementation": step.implementation.value,
                            "dependencies": ",".join(step.depends_on) or "none",
                            "orchestration_status": run.status.value,
                        },
                        priority=55,
                        updated_at=run.updated_at,
                    )
                )
        return items

    def _approval_item(
        self,
        *,
        kind: OperatorRequestKind,
        run_id: UUID,
        opportunity_id: UUID | None,
        title: str,
        summary: str,
        consequence: str,
        binding: str,
        details: dict[str, str],
        priority: int,
        updated_at,
    ) -> OperatorInboxItem:
        key = f"{kind.value}:{run_id}"
        semantic = {
            "key": key,
            "opportunity_id": str(opportunity_id) if opportunity_id else None,
            "title": title,
            "summary": summary,
            "consequence": consequence,
            "binding": binding,
            "details": details,
        }
        token = hashlib.sha256(
            json.dumps(
                semantic,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return OperatorInboxItem(
            key=key,
            kind=kind,
            request_type=OperatorRequestType.APPROVAL,
            run_id=run_id,
            opportunity_id=opportunity_id,
            venture_title=self._venture_title(opportunity_id),
            title=title,
            summary=summary,
            consequence=consequence,
            binding=binding,
            confirmation_token=token,
            action_command=f"helis-operator approve {key} --confirm {token}",
            details=details,
            priority=priority,
            updated_at=updated_at,
        )

    def _require_confirmed_approval(
        self,
        key: str,
        confirmation_token: str,
    ) -> OperatorInboxItem:
        item = self.get(key)
        if item is None:
            raise OperatorInboxError("operator request is missing, stale or already resolved")
        if item.request_type != OperatorRequestType.APPROVAL:
            raise OperatorInboxError("operator request requires observed input, not approval")
        if item.confirmation_token != confirmation_token.strip().lower():
            raise OperatorInboxError("confirmation token does not match the current request snapshot")
        return item

    def _cancel(self, item: OperatorInboxItem, reason: str) -> str:
        now = utc_now()
        if item.kind == OperatorRequestKind.VALIDATION:
            run = self.engine.store.get_experiment_run(item.run_id)
            if run is None or run.status != ExperimentRunStatus.WAITING_APPROVAL:
                raise OperatorInboxError("validation request is no longer waiting for approval")
            updated = run.model_copy(
                update={
                    "status": ExperimentRunStatus.CANCELLED,
                    "approval_granted": False,
                    "error": reason,
                    "updated_at": now,
                }
            )
            self.engine.record_experiment_run(updated, event_type="experiment.cancelled")
        elif item.kind == OperatorRequestKind.PREVIEW_PUBLICATION:
            run = self.previews.get_run(item.run_id)
            if run is None or run.status != PreviewPublishStatus.WAITING_APPROVAL:
                raise OperatorInboxError("publication request is no longer waiting for approval")
            updated = run.model_copy(
                update={
                    "status": PreviewPublishStatus.CANCELLED,
                    "approval_granted": False,
                    "error": reason,
                    "updated_at": now,
                }
            )
            self.previews.save_run(updated)
            self._cancel_event(item, updated.status.value, reason)
        elif item.kind == OperatorRequestKind.COMMERCE_CHECKOUT:
            run = self.commerce.get_run(item.run_id)
            if run is None or run.status != CheckoutRunStatus.WAITING_APPROVAL:
                raise OperatorInboxError("checkout request is no longer waiting for approval")
            updated = run.model_copy(
                update={
                    "status": CheckoutRunStatus.CANCELLED,
                    "approval_granted": False,
                    "error": reason,
                    "updated_at": now,
                }
            )
            self.commerce.save_run(updated)
            self._cancel_event(item, updated.status.value, reason)
        elif item.kind == OperatorRequestKind.OUTREACH:
            run = self.gtm.get_outreach_run(item.run_id)
            if run is None or run.status != OutreachRunStatus.WAITING_APPROVAL:
                raise OperatorInboxError("outreach request is no longer waiting for approval")
            updated = run.model_copy(
                update={
                    "status": OutreachRunStatus.CANCELLED,
                    "approval_granted": False,
                    "error": reason,
                    "updated_at": now,
                }
            )
            self.gtm.save_outreach_run(updated)
            self._cancel_event(item, updated.status.value, reason)
        elif item.kind == OperatorRequestKind.SELF_BRANCH:
            run = self.self_branches.get(item.run_id)
            if run is None or run.status != BranchMaterializationStatus.WAITING_APPROVAL:
                raise OperatorInboxError("branch request is no longer waiting for approval")
            updated = run.model_copy(
                update={
                    "status": BranchMaterializationStatus.CANCELLED,
                    "approval_granted": False,
                    "error": reason,
                    "updated_at": now,
                }
            )
            self.self_branches.save(updated)
            self._cancel_event(item, updated.status.value, reason)
        elif item.kind == OperatorRequestKind.SELF_MERGE:
            run = self.self_merges.get(item.run_id)
            if run is None or run.status != SelfImprovementMergeStatus.WAITING_APPROVAL:
                raise OperatorInboxError("merge request is no longer waiting for approval")
            updated = run.model_copy(
                update={
                    "status": SelfImprovementMergeStatus.CANCELLED,
                    "approval_granted": False,
                    "error": reason,
                    "updated_at": now,
                }
            )
            self.self_merges.save(updated)
            self._cancel_event(item, updated.status.value, reason)
        else:
            raise OperatorInboxError("input requests cannot be rejected")
        return updated.status.value

    def _cancel_event(self, item: OperatorInboxItem, status: str, reason: str) -> None:
        self.engine.store.append_event(
            AuditEvent(
                event_type=f"operator.{item.kind.value}_cancelled",
                entity_id=item.run_id,
                data={"status": status, "reason": reason},
            )
        )

    def _decision_event(
        self,
        item: OperatorInboxItem,
        receipt: OperatorDecisionReceipt,
    ) -> None:
        self.engine.store.append_event(
            AuditEvent(
                event_type=f"operator.inbox_{receipt.decision.value}",
                entity_id=item.run_id,
                data={
                    "key": item.key,
                    "kind": item.kind.value,
                    "opportunity_id": (
                        str(item.opportunity_id) if item.opportunity_id else None
                    ),
                    "confirmation_token": receipt.confirmation_token,
                    "resulting_status": receipt.resulting_status,
                    "reason": receipt.reason,
                },
            )
        )

    def _venture_title(self, opportunity_id: UUID | None) -> str:
        if opportunity_id is None:
            return "HELIS core"
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        return opportunity.title[:240] if opportunity is not None else f"missing:{opportunity_id}"
