from __future__ import annotations

import math
from uuid import UUID

from helis.cash_reservation import CashReservation, CashReservationManager
from helis.domain import Experiment, ExperimentRun
from helis.engine import HelisEngine
from helis.resource_envelope import EnvelopeConflict, ResourceEnvelopeManager


class ValidationCashCoordinator:
    """Binds paid external validation runs to two-phase cash reservations."""

    source = "validation-experiment"

    def __init__(self, engine: HelisEngine, envelope_id: UUID | None = None) -> None:
        self.engine = engine
        self.envelope_id = envelope_id

    @staticmethod
    def _key(run_id: UUID) -> str:
        return f"validation-run:{run_id}"

    def find_for_run(self, run_id: UUID) -> CashReservation | None:
        key = self._key(run_id)
        with self.engine.store.connect() as db:
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cash_reservations'"
            ).fetchone()
            if table is None:
                return None
            rows = db.execute(
                "SELECT payload FROM cash_reservations "
                "WHERE source = ? AND idempotency_key = ?",
                (self.source, key),
            ).fetchall()
        if len(rows) > 1:
            raise EnvelopeConflict("validation run is linked to multiple cash reservations")
        return CashReservation.model_validate_json(rows[0]["payload"]) if rows else None

    def reserve_for_run(
        self,
        run: ExperimentRun,
        experiment: Experiment,
    ) -> CashReservation | None:
        if experiment.max_cost_cents <= 0:
            return None

        existing = self.find_for_run(run.id)
        if existing is not None:
            if existing.opportunity_id != run.opportunity_id:
                raise EnvelopeConflict("cash reservation scope does not match validation run")
            return existing

        if self.envelope_id is None:
            raise EnvelopeConflict("paid external validation requires a resource envelope")
        envelopes = ResourceEnvelopeManager(self.engine)
        envelope = envelopes.get(self.envelope_id)
        if envelope is None:
            raise EnvelopeConflict("validation resource envelope does not exist")
        if envelope.opportunity_id != run.opportunity_id:
            raise EnvelopeConflict("validation resource envelope belongs to another venture")

        return CashReservationManager(self.engine).reserve(
            envelope.id,
            amount_cents=experiment.max_cost_cents,
            source=self.source,
            idempotency_key=self._key(run.id),
        )

    def settle_for_run(self, run_id: UUID, *, actual_cost_cents: float) -> CashReservation | None:
        reservation = self.find_for_run(run_id)
        if reservation is None:
            return None
        if actual_cost_cents < 0:
            raise ValueError("validation cash cost cannot be negative")
        settled_cents = math.ceil(actual_cost_cents)
        return CashReservationManager(self.engine).settle(
            reservation.id,
            actual_cents=settled_cents,
        )

    def release_for_run(self, run_id: UUID, *, reason: str) -> CashReservation | None:
        reservation = self.find_for_run(run_id)
        if reservation is None:
            return None
        return CashReservationManager(self.engine).release(reservation.id, reason=reason)
