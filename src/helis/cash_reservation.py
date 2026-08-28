from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.portfolio_value import VentureCostEvent, VentureEconomicsStore
from helis.resource_envelope import (
    EnvelopeConflict,
    EnvelopeExceeded,
    EnvelopeStatus,
    ResourceEnvelope,
)


class CashReservationStatus(StrEnum):
    RESERVED = "reserved"
    SETTLED = "settled"
    RELEASED = "released"


class CashReservation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    envelope_id: UUID
    opportunity_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    source: str = Field(min_length=2, max_length=200)
    idempotency_key: str = Field(min_length=2, max_length=500)
    reserved_cents: int = Field(gt=0)
    settled_cents: int = Field(default=0, ge=0)
    status: CashReservationStatus = CashReservationStatus.RESERVED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CashReservationManager:
    """Two-phase cash accounting: reserve capacity first, then settle actual cost or release."""

    def __init__(self, engine: HelisEngine) -> None:
        self.engine = engine
        self.store = engine.store
        self.economics = VentureEconomicsStore(engine)
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cash_reservations (
                    id TEXT PRIMARY KEY,
                    envelope_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    reserved_cents INTEGER NOT NULL,
                    settled_cents INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(envelope_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_cash_reservations_envelope_status
                    ON cash_reservations(envelope_id, status, created_at);

                CREATE TRIGGER IF NOT EXISTS trg_cash_reservation_capacity
                BEFORE INSERT ON cash_reservations
                WHEN NEW.status = 'reserved'
                BEGIN
                    SELECT CASE WHEN NEW.reserved_cents > (
                        SELECT
                            CAST(json_extract(payload, '$.cash_limit_cents') AS INTEGER)
                            - CAST(json_extract(payload, '$.cash_consumed_cents') AS INTEGER)
                            - COALESCE((
                                SELECT SUM(reserved_cents)
                                FROM cash_reservations
                                WHERE envelope_id = NEW.envelope_id AND status = 'reserved'
                            ), 0)
                        FROM resource_envelopes
                        WHERE id = NEW.envelope_id AND status = 'active'
                    ) THEN RAISE(ABORT, 'cash envelope reservation exceeded') END;
                END;

                CREATE TRIGGER IF NOT EXISTS trg_envelope_cash_update_guard
                BEFORE UPDATE OF payload ON resource_envelopes
                BEGIN
                    SELECT CASE WHEN (
                        CAST(json_extract(NEW.payload, '$.cash_consumed_cents') AS INTEGER)
                        + COALESCE((
                            SELECT SUM(reserved_cents)
                            FROM cash_reservations
                            WHERE envelope_id = NEW.id AND status = 'reserved'
                        ), 0)
                    ) > CAST(json_extract(NEW.payload, '$.cash_limit_cents') AS INTEGER)
                    THEN RAISE(ABORT, 'cash reservation would be overspent') END;
                END;

                CREATE TRIGGER IF NOT EXISTS trg_envelope_revoke_reservation_guard
                BEFORE UPDATE OF status ON resource_envelopes
                WHEN NEW.status = 'revoked'
                BEGIN
                    SELECT CASE WHEN EXISTS(
                        SELECT 1 FROM cash_reservations
                        WHERE envelope_id = NEW.id AND status = 'reserved'
                    ) THEN RAISE(ABORT, 'cannot revoke envelope with open cash reservation') END;
                END;
                """
            )

    def available_cash(self, envelope_id: UUID) -> int:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM resource_envelopes WHERE id = ?",
                (str(envelope_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"resource envelope not found: {envelope_id}")
            envelope = ResourceEnvelope.model_validate_json(row["payload"])
            reserved = db.execute(
                "SELECT COALESCE(SUM(reserved_cents), 0) AS total FROM cash_reservations "
                "WHERE envelope_id = ? AND status = ?",
                (str(envelope_id), CashReservationStatus.RESERVED.value),
            ).fetchone()["total"]
        return max(0, envelope.remaining_cash_cents - int(reserved))

    def list(self, envelope_id: UUID | None = None) -> list[CashReservation]:
        with self.store.connect() as db:
            if envelope_id is None:
                rows = db.execute(
                    "SELECT payload FROM cash_reservations ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM cash_reservations WHERE envelope_id = ? "
                    "ORDER BY created_at DESC",
                    (str(envelope_id),),
                ).fetchall()
        return [CashReservation.model_validate_json(row["payload"]) for row in rows]

    def get(self, reservation_id: UUID) -> CashReservation | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM cash_reservations WHERE id = ?",
                (str(reservation_id),),
            ).fetchone()
        return CashReservation.model_validate_json(row["payload"]) if row else None

    def reserve(
        self,
        envelope_id: UUID,
        *,
        amount_cents: int,
        source: str,
        idempotency_key: str,
    ) -> CashReservation:
        if amount_cents <= 0:
            raise ValueError("cash reservation must be positive")
        now = utc_now()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload FROM cash_reservations "
                "WHERE envelope_id = ? AND idempotency_key = ?",
                (str(envelope_id), idempotency_key),
            ).fetchone()
            if prior:
                existing = CashReservation.model_validate_json(prior["payload"])
                if existing.reserved_cents != amount_cents or existing.source != source:
                    raise EnvelopeConflict("cash reservation key reused with different request")
                return existing

            row = db.execute(
                "SELECT payload FROM resource_envelopes WHERE id = ?",
                (str(envelope_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"resource envelope not found: {envelope_id}")
            envelope = ResourceEnvelope.model_validate_json(row["payload"])
            if envelope.status != EnvelopeStatus.ACTIVE:
                raise EnvelopeExceeded(f"envelope is {envelope.status.value}")
            reservation = CashReservation(
                envelope_id=envelope.id,
                opportunity_id=envelope.opportunity_id,
                currency=envelope.currency,
                source=source,
                idempotency_key=idempotency_key,
                reserved_cents=amount_cents,
                created_at=now,
                updated_at=now,
            )
            try:
                db.execute(
                    "INSERT INTO cash_reservations "
                    "(id, envelope_id, opportunity_id, status, source, idempotency_key, "
                    "reserved_cents, settled_cents, payload, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(reservation.id),
                        str(reservation.envelope_id),
                        str(reservation.opportunity_id),
                        reservation.status.value,
                        reservation.source,
                        reservation.idempotency_key,
                        reservation.reserved_cents,
                        reservation.settled_cents,
                        reservation.model_dump_json(),
                        reservation.created_at.isoformat(),
                        reservation.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise EnvelopeExceeded(str(exc)) from exc

        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.cash_reserved",
                entity_id=reservation.id,
                data={
                    "envelope_id": str(envelope_id),
                    "amount_cents": amount_cents,
                    "currency": reservation.currency,
                    "source": source,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        return reservation

    def settle(self, reservation_id: UUID, *, actual_cents: int) -> CashReservation:
        if actual_cents < 0:
            raise ValueError("settled cash cannot be negative")
        now = utc_now()
        cost: VentureCostEvent | None = None
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM cash_reservations WHERE id = ?",
                (str(reservation_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"cash reservation not found: {reservation_id}")
            reservation = CashReservation.model_validate_json(row["payload"])
            if reservation.status == CashReservationStatus.SETTLED:
                if reservation.settled_cents != actual_cents:
                    raise EnvelopeConflict("settled reservation cannot change actual cost")
                return reservation
            if reservation.status != CashReservationStatus.RESERVED:
                raise EnvelopeConflict(f"reservation is {reservation.status.value}")
            if actual_cents > reservation.reserved_cents:
                raise EnvelopeExceeded("actual cash cost exceeds reserved amount")

            envelope_row = db.execute(
                "SELECT payload FROM resource_envelopes WHERE id = ?",
                (str(reservation.envelope_id),),
            ).fetchone()
            if envelope_row is None:
                raise EnvelopeConflict("reservation envelope no longer exists")
            envelope = ResourceEnvelope.model_validate_json(envelope_row["payload"])
            settled = reservation.model_copy(
                update={
                    "status": CashReservationStatus.SETTLED,
                    "settled_cents": actual_cents,
                    "updated_at": now,
                }
            )
            db.execute(
                "UPDATE cash_reservations SET status = ?, settled_cents = ?, payload = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    settled.status.value,
                    settled.settled_cents,
                    settled.model_dump_json(),
                    settled.updated_at.isoformat(),
                    str(settled.id),
                ),
            )

            updated_envelope = envelope.model_copy(
                update={
                    "cash_consumed_cents": envelope.cash_consumed_cents + actual_cents,
                    "updated_at": now,
                }
            )
            if (
                updated_envelope.remaining_cash_cents == 0
                and updated_envelope.remaining_model_calls == 0
            ):
                updated_envelope = updated_envelope.model_copy(
                    update={"status": EnvelopeStatus.EXHAUSTED}
                )
            db.execute(
                "UPDATE resource_envelopes SET status = ?, payload = ?, updated_at = ? WHERE id = ?",
                (
                    updated_envelope.status.value,
                    updated_envelope.model_dump_json(),
                    updated_envelope.updated_at.isoformat(),
                    str(updated_envelope.id),
                ),
            )
            if actual_cents > 0:
                cost = VentureCostEvent(
                    opportunity_id=reservation.opportunity_id,
                    amount_cents=actual_cents,
                    currency=reservation.currency,
                    source=f"reservation:{reservation.source}",
                    external_ref=str(reservation.id),
                    recorded_at=now,
                )
                db.execute(
                    "INSERT INTO venture_cost_events "
                    "(id, opportunity_id, amount_cents, currency, source, external_ref, payload, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(cost.id),
                        str(cost.opportunity_id),
                        cost.amount_cents,
                        cost.currency,
                        cost.source,
                        cost.external_ref,
                        cost.model_dump_json(),
                        cost.recorded_at.isoformat(),
                    ),
                )

        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.cash_settled",
                entity_id=reservation_id,
                data={
                    "reserved_cents": reservation.reserved_cents,
                    "actual_cents": actual_cents,
                    "released_cents": reservation.reserved_cents - actual_cents,
                    "cost_event_id": str(cost.id) if cost else None,
                },
            )
        )
        return settled

    def release(self, reservation_id: UUID, *, reason: str) -> CashReservation:
        now = utc_now()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM cash_reservations WHERE id = ?",
                (str(reservation_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"cash reservation not found: {reservation_id}")
            reservation = CashReservation.model_validate_json(row["payload"])
            if reservation.status == CashReservationStatus.RELEASED:
                return reservation
            if reservation.status != CashReservationStatus.RESERVED:
                raise EnvelopeConflict(f"reservation is {reservation.status.value}")
            released = reservation.model_copy(
                update={"status": CashReservationStatus.RELEASED, "updated_at": now}
            )
            db.execute(
                "UPDATE cash_reservations SET status = ?, payload = ?, updated_at = ? WHERE id = ?",
                (
                    released.status.value,
                    released.model_dump_json(),
                    released.updated_at.isoformat(),
                    str(released.id),
                ),
            )
        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.cash_released",
                entity_id=reservation_id,
                data={"reserved_cents": reservation.reserved_cents, "reason": reason},
            )
        )
        return released
