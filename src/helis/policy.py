from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ActionKind(StrEnum):
    RESEARCH = "research"
    NETWORK_READ = "network_read"
    FILE_WRITE = "file_write"
    SANDBOX_EXECUTION = "sandbox_execution"
    NETWORK_WRITE = "network_write"
    CHECKOUT_CREATE = "checkout_create"
    EXTERNAL_CONTACT = "external_contact"
    PUBLICATION = "publication"
    SPEND = "spend"
    CREDENTIAL_ACCESS = "credential_access"
    SELF_MODIFY = "self_modify"


class ActionRequest(BaseModel):
    kind: ActionKind
    description: str
    estimated_cost_cents: int = Field(default=0, ge=0)
    reversible: bool = True


class PolicyDecision(BaseModel):
    allowed: bool
    requires_approval: bool
    reason: str


class AutonomyPolicy(BaseModel):
    autonomous_spend_limit_cents: int = Field(default=0, ge=0)
    allow_checkout_creation_without_approval: bool = False
    allow_publication_without_approval: bool = False
    allow_external_contact_without_approval: bool = False
    allow_self_modification_without_approval: bool = False

    def evaluate(self, action: ActionRequest) -> PolicyDecision:
        safe_autonomous = {
            ActionKind.RESEARCH,
            ActionKind.NETWORK_READ,
            ActionKind.FILE_WRITE,
            ActionKind.SANDBOX_EXECUTION,
        }
        if action.kind in safe_autonomous:
            return PolicyDecision(allowed=True, requires_approval=False, reason="autonomous_safe_class")

        if action.kind == ActionKind.SPEND:
            allowed = action.estimated_cost_cents <= self.autonomous_spend_limit_cents
            return PolicyDecision(
                allowed=allowed,
                requires_approval=not allowed,
                reason="within_spend_limit" if allowed else "spend_limit_exceeded",
            )

        if action.kind == ActionKind.CHECKOUT_CREATE:
            allowed = self.allow_checkout_creation_without_approval
            return PolicyDecision(
                allowed=allowed,
                requires_approval=not allowed,
                reason="checkout_creation_gate",
            )

        if action.kind == ActionKind.PUBLICATION:
            allowed = self.allow_publication_without_approval
            return PolicyDecision(allowed=allowed, requires_approval=not allowed, reason="publication_gate")

        if action.kind == ActionKind.EXTERNAL_CONTACT:
            allowed = self.allow_external_contact_without_approval
            return PolicyDecision(allowed=allowed, requires_approval=not allowed, reason="contact_gate")

        if action.kind == ActionKind.SELF_MODIFY:
            allowed = self.allow_self_modification_without_approval
            return PolicyDecision(allowed=allowed, requires_approval=not allowed, reason="self_modify_gate")

        return PolicyDecision(allowed=False, requires_approval=True, reason="sensitive_or_unknown_action")
