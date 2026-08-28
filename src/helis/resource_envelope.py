from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.budget import BudgetExceeded, CycleBudget
from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.portfolio import PortfolioPlan, PortfolioStore
from helis.portfolio_value import VentureCostEvent, VentureEconomicsStore


class EnvelopeExceeded(BudgetExceeded):
    pass


class EnvelopeConflict(RuntimeError):
    pass


class EnvelopeStatus(StrEnum):
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    REVOKED = "revoked"


class ResourceEnvelope(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    opportunity_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    cash_limit_cents: int = Field(ge=0)
    model_call_limit: int = Field(ge=0)
    cash_consumed_cents: int = Field(default=0, ge=0)
    model_calls_consumed: int = Field(default=0, ge=0)
    status: EnvelopeStatus = EnvelopeStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def remaining_cash_cents(self) -> int:
        return max(0, self.cash_limit_cents - self.cash_consumed_cents)

    @property
    def remaining_model_calls(self) -> int:
        return max(0, self.model_call_limit - self.model_calls_consumed)


class EnvelopeConsumption(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    envelope_id: UUID
    opportunity_id: UUID
    source: str = Field(min_length=2, max_length=200)
    idempotency_key: str = Field(min_length=2, max_length=500)
    cash_cents: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class ResourceEnvelopeManager:
    """Enforces portfolio resource ceilings. It does not perform payments or model calls."""

    def __init__(self, engine: HelisEngine) -> None:
        self.engine = engine
        self.store = engine.store
        self.economics = VentureEconomicsStore(engine)
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS resource_envelopes (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(plan_id, opportunity_id)
                );
                CREATE INDEX IF NOT EXISTS idx_envelopes_status
                    ON resource_envelopes(status, updated_at);
                CREATE TABLE IF NOT EXISTS envelope_consumptions (
                    id TEXT PRIMARY KEY,
                    envelope_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(envelope_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_envelope_consumptions_envelope
                    ON envelope_consumptions(envelope_id, created_at);
                """
            )

    def activate(self, plan: PortfolioPlan) -> list[ResourceEnvelope]:
        latest = PortfolioStore(self.engine).latest()
        if latest is None or latest.id != plan.id:
            raise EnvelopeConflict("only the latest portfolio plan may be activated")

        created: list[ResourceEnvelope] = []
        now = utc_now()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id, payload FROM resource_envelopes WHERE status = ? AND plan_id != ?",
                (EnvelopeStatus.ACTIVE.value, str(plan.id)),
            ).fetchall()
            for row in rows:
                existing = ResourceEnvelope.model_validate_json(row["payload"])
                revoked = existing.model_copy(
                    update={"status": EnvelopeStatus.REVOKED, "updated_at": now}
                )
                db.execute(
                    "UPDATE resource_envelopes SET status = ?, payload = ?, updated_at = ? WHERE id = ?",
                    (
                        revoked.status.value,
                        revoked.model_dump_json(),
                        revoked.updated_at.isoformat(),
                        str(revoked.id),
                    ),
                )

            for allocation in plan.allocations:
                row = db.execute(
                    "SELECT payload FROM resource_envelopes "
                    "WHERE plan_id = ? AND opportunity_id = ?",
                    (str(plan.id), str(allocation.opportunity_id)),
                ).fetchone()
                if row:
                    envelope = ResourceEnvelope.model_validate_json(row["payload"])
                else:
                    envelope = ResourceEnvelope(
                        plan_id=plan.id,
                        opportunity_id=allocation.opportunity_id,
                        currency=plan.budget.currency,
                        cash_limit_cents=allocation.cash_cents,
                        model_call_limit=allocation.model_calls,
                    )
                    db.execute(
                        "INSERT INTO resource_envelopes "
                        "(id, plan_id, opportunity_id, status, payload, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(envelope.id),
                            str(envelope.plan_id),
                            str(envelope.opportunity_id),
                            envelope.status.value,
                            envelope.model_dump_json(),
                            envelope.created_at.isoformat(),
                            envelope.updated_at.isoformat(),
                        ),
                    )
                created.append(envelope)

        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.envelopes_activated",
                entity_id=plan.id,
                data={
                    "plan_id": str(plan.id),
                    "envelope_count": len(created),
                    "currency": plan.budget.currency,
                },
            )
        )
        return created

    def get(self, envelope_id: UUID) -> ResourceEnvelope | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM resource_envelopes WHERE id = ?",
                (str(envelope_id),),
            ).fetchone()
        return ResourceEnvelope.model_validate_json(row["payload"]) if row else None

    def list(self, *, status: EnvelopeStatus | None = None) -> list[ResourceEnvelope]:
        with self.store.connect() as db:
            if status is None:
                rows = db.execute(
                    "SELECT payload FROM resource_envelopes ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM resource_envelopes WHERE status = ? ORDER BY created_at DESC",
                    (status.value,),
                ).fetchall()
        return [ResourceEnvelope.model_validate_json(row["payload"]) for row in rows]

    def consume(
        self,
        envelope_id: UUID,
        *,
        source: str,
        idempotency_key: str,
        cash_cents: int = 0,
        model_calls: int = 0,
    ) -> ResourceEnvelope:
        if cash_cents < 0 or model_calls < 0 or (cash_cents == 0 and model_calls == 0):
            raise ValueError("consumption must contain positive cash or model-call usage")
        now = utc_now()
        new_cost: VentureCostEvent | None = None
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload FROM envelope_consumptions "
                "WHERE envelope_id = ? AND idempotency_key = ?",
                (str(envelope_id), idempotency_key),
            ).fetchone()
            if prior:
                recorded = EnvelopeConsumption.model_validate_json(prior["payload"])
                if (
                    recorded.cash_cents != cash_cents
                    or recorded.model_calls != model_calls
                    or recorded.source != source
                ):
                    raise EnvelopeConflict("idempotency key was reused with different consumption")
                row = db.execute(
                    "SELECT payload FROM resource_envelopes WHERE id = ?",
                    (str(envelope_id),),
                ).fetchone()
                if row is None:
                    raise EnvelopeConflict("envelope disappeared after recorded consumption")
                return ResourceEnvelope.model_validate_json(row["payload"])

            row = db.execute(
                "SELECT payload FROM resource_envelopes WHERE id = ?",
                (str(envelope_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"resource envelope not found: {envelope_id}")
            envelope = ResourceEnvelope.model_validate_json(row["payload"])
            if envelope.status != EnvelopeStatus.ACTIVE:
                raise EnvelopeExceeded(f"envelope is {envelope.status.value}")
            if cash_cents > envelope.remaining_cash_cents:
                raise EnvelopeExceeded("cash envelope exhausted")
            if model_calls > envelope.remaining_model_calls:
                raise EnvelopeExceeded("model-call envelope exhausted")

            updated = envelope.model_copy(
                update={
                    "cash_consumed_cents": envelope.cash_consumed_cents + cash_cents,
                    "model_calls_consumed": envelope.model_calls_consumed + model_calls,
                    "updated_at": now,
                }
            )
            if updated.remaining_cash_cents == 0 and updated.remaining_model_calls == 0:
                updated = updated.model_copy(update={"status": EnvelopeStatus.EXHAUSTED})
            consumption = EnvelopeConsumption(
                envelope_id=envelope.id,
                opportunity_id=envelope.opportunity_id,
                source=source,
                idempotency_key=idempotency_key,
                cash_cents=cash_cents,
                model_calls=model_calls,
                created_at=now,
            )
            db.execute(
                "UPDATE resource_envelopes SET status = ?, payload = ?, updated_at = ? WHERE id = ?",
                (
                    updated.status.value,
                    updated.model_dump_json(),
                    updated.updated_at.isoformat(),
                    str(updated.id),
                ),
            )
            db.execute(
                "INSERT INTO envelope_consumptions "
                "(id, envelope_id, opportunity_id, source, idempotency_key, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(consumption.id),
                    str(consumption.envelope_id),
                    str(consumption.opportunity_id),
                    consumption.source,
                    consumption.idempotency_key,
                    consumption.model_dump_json(),
                    consumption.created_at.isoformat(),
                ),
            )
            if cash_cents > 0:
                new_cost = VentureCostEvent(
                    opportunity_id=envelope.opportunity_id,
                    amount_cents=cash_cents,
                    currency=envelope.currency,
                    source=f"envelope:{source}",
                    external_ref=idempotency_key,
                    recorded_at=now,
                )
                db.execute(
                    "INSERT INTO venture_cost_events "
                    "(id, opportunity_id, amount_cents, currency, source, external_ref, payload, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(new_cost.id),
                        str(new_cost.opportunity_id),
                        new_cost.amount_cents,
                        new_cost.currency,
                        new_cost.source,
                        new_cost.external_ref,
                        new_cost.model_dump_json(),
                        new_cost.recorded_at.isoformat(),
                    ),
                )

        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.envelope_consumed",
                entity_id=envelope_id,
                data={
                    "source": source,
                    "idempotency_key": idempotency_key,
                    "cash_cents": cash_cents,
                    "model_calls": model_calls,
                    "cost_event_id": str(new_cost.id) if new_cost else None,
                },
            )
        )
        return updated

    def revoke(self, envelope_id: UUID, *, reason: str) -> ResourceEnvelope:
        envelope = self.get(envelope_id)
        if envelope is None:
            raise ValueError(f"resource envelope not found: {envelope_id}")
        if envelope.status == EnvelopeStatus.REVOKED:
            return envelope
        revoked = envelope.model_copy(
            update={"status": EnvelopeStatus.REVOKED, "updated_at": utc_now()}
        )
        with self.store.connect() as db:
            db.execute(
                "UPDATE resource_envelopes SET status = ?, payload = ?, updated_at = ? WHERE id = ?",
                (
                    revoked.status.value,
                    revoked.model_dump_json(),
                    revoked.updated_at.isoformat(),
                    str(revoked.id),
                ),
            )
        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.envelope_revoked",
                entity_id=envelope_id,
                data={"reason": reason},
            )
        )
        return revoked

    def model_budget(
        self,
        envelope_id: UUID,
        *,
        max_tokens: int = 40_000,
        max_model_cost_cents: float = 25.0,
    ) -> EnvelopeCycleBudget:
        envelope = self.get(envelope_id)
        if envelope is None:
            raise ValueError(f"resource envelope not found: {envelope_id}")
        if envelope.status != EnvelopeStatus.ACTIVE:
            raise EnvelopeExceeded(f"envelope is {envelope.status.value}")
        return EnvelopeCycleBudget(
            manager=self,
            envelope_id=envelope_id,
            max_model_calls=envelope.remaining_model_calls,
            max_tokens=max_tokens,
            max_cost_cents=max_model_cost_cents,
        )


class EnvelopeCycleBudget(CycleBudget):
    """CycleBudget whose attempted model calls are pre-reserved from a persistent envelope."""

    def __init__(
        self,
        *,
        manager: ResourceEnvelopeManager,
        envelope_id: UUID,
        max_model_calls: int,
        max_tokens: int,
        max_cost_cents: float,
    ) -> None:
        super().__init__(
            max_model_calls=max_model_calls,
            max_tokens=max_tokens,
            max_cost_cents=max_cost_cents,
        )
        self.manager = manager
        self.envelope_id = envelope_id

    def ensure_call_available(self) -> None:
        if self.model_calls >= self.max_model_calls:
            raise EnvelopeExceeded("model call budget exhausted")
        self.manager.consume(
            self.envelope_id,
            source="model-call",
            idempotency_key=f"model-call:{uuid4()}",
            model_calls=1,
        )
        # Reserve before the external model request. Failed attempts still consume capacity.
        self.model_calls += 1

    def record(self, result: ModelResult) -> None:
        self.tokens += result.total_tokens
        self.cost_cents += result.estimated_cost_cents
        if self.tokens > self.max_tokens:
            raise BudgetExceeded("token budget exceeded")
        if self.cost_cents > self.max_cost_cents:
            raise BudgetExceeded("cost budget exceeded")
