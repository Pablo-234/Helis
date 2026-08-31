from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from helis.commerce_domain import (
    BillingMode,
    CheckoutBinding,
    CheckoutRun,
    CheckoutRunStatus,
    CommerceBuildContext,
    CommerceOffer,
    CommerceRevenueEvent,
    PaymentResultStatus,
)
from helis.commerce_gateway import CommerceGateway, validate_checkout_url
from helis.commerce_store import CommerceStore
from helis.domain import (
    AuditEvent,
    DeliveryModel,
    Opportunity,
    RevenueModel,
    VentureStage,
    utc_now,
)
from helis.engine import HelisEngine
from helis.policy import ActionKind, ActionRequest, AutonomyPolicy

_SELF_SERVE_DELIVERY = frozenset(
    {
        DeliveryModel.SOFTWARE,
        DeliveryModel.DATA_PRODUCT,
        DeliveryModel.CONTENT_MEDIA,
    }
)
_SELF_SERVE_REVENUE = frozenset(
    {
        RevenueModel.FIXED_FEE,
        RevenueModel.SUBSCRIPTION,
        RevenueModel.LICENSING,
    }
)


@dataclass(slots=True)
class CommerceTickReport:
    offer: CommerceOffer | None = None
    run: CheckoutRun | None = None
    binding: CheckoutBinding | None = None
    revenue: CommerceRevenueEvent | None = None
    reason: str = "no_commerce_work"
    created: bool = False
    binding_created: bool = False
    revenue_created: bool = False

    @property
    def did_work(self) -> bool:
        return self.created or self.binding_created or self.revenue_created


class CommerceError(RuntimeError):
    pass


class CommerceManager:
    """Deterministic commerce boundary for self-serve venture offers and observed payments."""

    def __init__(
        self,
        engine: HelisEngine,
        *,
        gateway: CommerceGateway | None = None,
        policy: AutonomyPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.state = CommerceStore(engine.store)
        self.gateway = gateway
        self.policy = policy or AutonomyPolicy()

    @staticmethod
    def is_eligible(opportunity: Opportunity) -> bool:
        model = opportunity.business_model
        return bool(
            model is not None
            and model.delivery_model in _SELF_SERVE_DELIVERY
            and model.revenue_model in _SELF_SERVE_REVENUE
        )

    def advance_prebuild(self, opportunity_id: UUID) -> CommerceTickReport:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            return CommerceTickReport(reason="commerce_venture_missing")
        if not self.is_eligible(opportunity):
            return CommerceTickReport(reason="commerce_not_applicable")
        if opportunity.stage not in {VentureStage.VALIDATED, VentureStage.BUILDING}:
            return CommerceTickReport(reason=f"commerce_stage_not_prebuild:{opportunity.stage.value}")

        offer = self._ensure_offer(opportunity)
        if offer is None:
            return CommerceTickReport(reason="commerce_positive_price_missing")
        run = self.state.get_run_for_offer(offer.id)
        created = False
        if run is None:
            decision = self.policy.evaluate(
                ActionRequest(
                    kind=ActionKind.CHECKOUT_CREATE,
                    description=(
                        f"create checkout for venture {opportunity.id} at {offer.display_price}"
                    ),
                )
            )
            autonomous = decision.allowed and not decision.requires_approval
            run = CheckoutRun(
                offer_id=offer.id,
                opportunity_id=opportunity.id,
                offer_hash=offer.offer_hash,
                status=CheckoutRunStatus.READY if autonomous else CheckoutRunStatus.WAITING_APPROVAL,
                approval_granted=autonomous,
            )
            self._save_run(run, "commerce.checkout_planned")
            created = True

        binding = self.state.get_binding_for_run(run.id)
        if binding is not None:
            if run.status != CheckoutRunStatus.ACTIVE:
                run = run.model_copy(
                    update={"status": CheckoutRunStatus.ACTIVE, "updated_at": utc_now()}
                )
                self._save_run(run, "commerce.checkout_recovered")
            return CommerceTickReport(
                offer=offer,
                run=run,
                binding=binding,
                reason="commerce_checkout_active",
                created=created,
            )

        if run.status == CheckoutRunStatus.WAITING_APPROVAL:
            return CommerceTickReport(
                offer=offer,
                run=run,
                reason="commerce_checkout_waiting_approval",
                created=created,
            )
        if run.status in {CheckoutRunStatus.BLOCKED, CheckoutRunStatus.FAILED}:
            return CommerceTickReport(
                offer=offer,
                run=run,
                reason=f"commerce_checkout_{run.status.value}:{run.error or 'unknown'}",
                created=created,
            )
        if run.status != CheckoutRunStatus.READY or not run.approval_granted:
            return CommerceTickReport(
                offer=offer,
                run=run,
                reason=f"commerce_checkout_not_ready:{run.status.value}",
                created=created,
            )
        if self.gateway is None:
            return CommerceTickReport(
                offer=offer,
                run=run,
                reason="commerce_gateway_missing",
                created=created,
            )

        try:
            ack = self.gateway.create_checkout(run, offer)
            validate_checkout_url(ack.checkout_url)
        except Exception as exc:  # noqa: BLE001 -- external boundary failure is persisted and isolated
            failed = run.model_copy(
                update={
                    "status": CheckoutRunStatus.FAILED,
                    "error": f"{type(exc).__name__}: {exc}",
                    "updated_at": utc_now(),
                }
            )
            self._save_run(failed, "commerce.checkout_failed")
            return CommerceTickReport(
                offer=offer,
                run=failed,
                reason=f"commerce_checkout_failed:{failed.error}",
                created=created,
            )

        binding = self.state.save_binding(
            CheckoutBinding(
                run_id=run.id,
                offer_id=offer.id,
                opportunity_id=opportunity.id,
                offer_hash=offer.offer_hash,
                checkout_url=ack.checkout_url,
                external_ref=ack.external_ref,
                metadata=dict(ack.metadata),
            )
        )
        active = run.model_copy(
            update={
                "status": CheckoutRunStatus.ACTIVE,
                "destination": self.gateway.safe_destination,
                "external_ref": ack.external_ref,
                "updated_at": utc_now(),
            }
        )
        self._save_run(active, "commerce.checkout_activated")
        self.engine.store.append_event(
            AuditEvent(
                event_type="commerce.checkout_bound",
                entity_id=binding.id,
                data={
                    "opportunity_id": str(opportunity.id),
                    "offer_id": str(offer.id),
                    "offer_hash": offer.offer_hash,
                    "checkout_url": binding.checkout_url,
                    "external_ref": binding.external_ref,
                },
            )
        )
        return CommerceTickReport(
            offer=offer,
            run=active,
            binding=binding,
            reason="commerce_checkout_activated",
            created=created,
            binding_created=True,
        )

    def approve(self, run_id: UUID) -> CheckoutRun:
        run = self._require_run(run_id)
        if run.status in {CheckoutRunStatus.READY, CheckoutRunStatus.ACTIVE} and run.approval_granted:
            return run
        if run.status != CheckoutRunStatus.WAITING_APPROVAL:
            raise CommerceError(
                f"checkout run {run.id} cannot be approved from {run.status.value}"
            )
        approved = run.model_copy(
            update={
                "status": CheckoutRunStatus.READY,
                "approval_granted": True,
                "updated_at": utc_now(),
            }
        )
        self._save_run(approved, "commerce.checkout_approved")
        return approved

    def build_context(self, opportunity_id: UUID) -> CommerceBuildContext | None:
        binding = self.state.latest_binding(opportunity_id)
        if binding is None:
            return None
        offer = self.state.get_offer(binding.offer_id)
        run = self.state.get_run(binding.run_id)
        if offer is None or run is None or run.status != CheckoutRunStatus.ACTIVE:
            return None
        if offer.offer_hash != binding.offer_hash or run.offer_hash != binding.offer_hash:
            return None
        return CommerceBuildContext(
            offer_id=offer.id,
            offer_hash=offer.offer_hash,
            checkout_url=binding.checkout_url,
            price_cents=offer.price_cents,
            currency=offer.currency,
            display_price=offer.display_price,
            billing_mode=offer.billing_mode,
        )

    def poll_payment(self, opportunity_id: UUID) -> CommerceTickReport:
        binding = self.state.latest_binding(opportunity_id)
        if binding is None:
            return CommerceTickReport(reason="commerce_checkout_missing")
        offer = self.state.get_offer(binding.offer_id)
        run = self.state.get_run(binding.run_id)
        if offer is None or run is None or run.status != CheckoutRunStatus.ACTIVE:
            return CommerceTickReport(reason="commerce_checkout_not_active")
        if self.gateway is None:
            return CommerceTickReport(
                offer=offer,
                run=run,
                binding=binding,
                reason="commerce_payment_gateway_missing",
            )

        try:
            result = self.gateway.poll_payment(binding)
        except Exception as exc:  # noqa: BLE001 -- payment observation failure must not fabricate outcome
            self.engine.store.append_event(
                AuditEvent(
                    event_type="commerce.payment_poll_failed",
                    entity_id=binding.id,
                    data={
                        "opportunity_id": str(opportunity_id),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            )
            return CommerceTickReport(
                offer=offer,
                run=run,
                binding=binding,
                reason=f"commerce_payment_poll_failed:{type(exc).__name__}",
            )
        if result is None or result.status == PaymentResultStatus.PENDING:
            return CommerceTickReport(
                offer=offer,
                run=run,
                binding=binding,
                reason="commerce_payment_pending",
            )

        assert result.external_ref is not None
        assert result.currency is not None
        existing = self.state.get_revenue_by_external_ref(
            "self_serve_checkout", result.external_ref
        )
        if existing is not None:
            return CommerceTickReport(
                offer=offer,
                run=run,
                binding=binding,
                revenue=existing,
                reason="commerce_payment_already_recorded",
            )
        if result.amount_cents != offer.price_cents or result.currency.upper() != offer.currency:
            self.engine.store.append_event(
                AuditEvent(
                    event_type="commerce.payment_rejected",
                    entity_id=binding.id,
                    data={
                        "opportunity_id": str(opportunity_id),
                        "external_ref": result.external_ref,
                        "observed_amount_cents": result.amount_cents,
                        "observed_currency": result.currency.upper(),
                        "expected_amount_cents": offer.price_cents,
                        "expected_currency": offer.currency,
                    },
                )
            )
            return CommerceTickReport(
                offer=offer,
                run=run,
                binding=binding,
                reason="commerce_payment_amount_or_currency_mismatch",
            )

        revenue = CommerceRevenueEvent(
            opportunity_id=opportunity_id,
            offer_id=offer.id,
            checkout_id=binding.id,
            amount_cents=result.amount_cents,
            currency=result.currency.upper(),
            external_ref=result.external_ref,
        )
        saved = self.state.save_revenue(revenue)
        created = saved.id == revenue.id
        if created:
            self.engine.store.append_event(
                AuditEvent(
                    event_type="commerce.revenue_recorded",
                    entity_id=saved.id,
                    data={
                        "opportunity_id": str(opportunity_id),
                        "offer_id": str(offer.id),
                        "checkout_id": str(binding.id),
                        "amount_cents": saved.amount_cents,
                        "currency": saved.currency,
                        "external_ref": saved.external_ref,
                    },
                )
            )
        return CommerceTickReport(
            offer=offer,
            run=run,
            binding=binding,
            revenue=saved,
            reason="commerce_payment_recorded" if created else "commerce_payment_already_recorded",
            revenue_created=created,
        )

    def _ensure_offer(self, opportunity: Opportunity) -> CommerceOffer | None:
        model = opportunity.business_model
        if model is None:
            return None
        price = model.pricing.low_cents if model.pricing.low_cents > 0 else model.pricing.high_cents
        if price <= 0:
            return None
        payload = {
            "opportunity_id": str(opportunity.id),
            "business_model": model.model_dump(mode="json"),
            "selected_price_cents": price,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        offer_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self.state.get_offer_by_hash(opportunity.id, offer_hash)
        if existing is not None:
            return existing
        billing = (
            BillingMode.SUBSCRIPTION
            if model.revenue_model == RevenueModel.SUBSCRIPTION
            else BillingMode.ONE_TIME
        )
        offer = CommerceOffer(
            opportunity_id=opportunity.id,
            offer_hash=offer_hash,
            name=model.name,
            description=model.offer,
            price_cents=price,
            currency=model.pricing.currency,
            pricing_unit=model.pricing.unit,
            billing_mode=billing,
            revenue_model=model.revenue_model,
            delivery_model=model.delivery_model,
        )
        saved = self.state.save_offer(offer)
        if saved.id == offer.id:
            self.engine.store.append_event(
                AuditEvent(
                    event_type="commerce.offer_created",
                    entity_id=offer.id,
                    data={
                        "opportunity_id": str(opportunity.id),
                        "offer_hash": offer_hash,
                        "price_cents": offer.price_cents,
                        "currency": offer.currency,
                        "billing_mode": offer.billing_mode.value,
                    },
                )
            )
        return saved

    def _require_run(self, run_id: UUID) -> CheckoutRun:
        run = self.state.get_run(run_id)
        if run is None:
            raise CommerceError(f"checkout run not found: {run_id}")
        return run

    def _save_run(self, run: CheckoutRun, event_type: str) -> None:
        self.state.save_run(run)
        self.engine.store.append_event(
            AuditEvent(
                event_type=event_type,
                entity_id=run.id,
                data={
                    "offer_id": str(run.offer_id),
                    "opportunity_id": str(run.opportunity_id),
                    "offer_hash": run.offer_hash,
                    "status": run.status.value,
                    "approval_granted": run.approval_granted,
                    "destination": run.destination,
                    "external_ref": run.external_ref,
                    "error": run.error,
                },
            )
        )
