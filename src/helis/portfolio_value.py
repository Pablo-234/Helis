from __future__ import annotations

import math
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.gtm_domain import LeadResponseKind
from helis.gtm_store import GTMStore


class VentureCostEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    amount_cents: int = Field(gt=0)
    currency: str = Field(default="PLN", min_length=3, max_length=3)
    source: str = Field(min_length=2, max_length=200)
    external_ref: str | None = Field(default=None, max_length=500)
    recorded_at: datetime = Field(default_factory=utc_now)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class VentureValueEstimate(BaseModel):
    opportunity_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    resolved_outcomes: int = Field(ge=0)
    paid_sales: int = Field(ge=0)
    observed_revenue_cents: int = Field(ge=0)
    observed_cost_cents: int = Field(ge=0)
    realized_net_cents: int
    posterior_paid_sale_probability: float = Field(ge=0, le=1)
    average_paid_sale_value_cents: float = Field(ge=0)
    expected_revenue_per_next_resolved_contact_cents: float = Field(ge=0)
    observed_cost_per_resolved_contact_cents: float = Field(ge=0)
    expected_net_per_next_resolved_contact_cents: float
    evidence_confidence: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)
    realized_roi: float | None = None


class VentureEconomicsStore:
    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS venture_cost_events (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    source TEXT NOT NULL,
                    external_ref TEXT,
                    payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_venture_costs_opportunity
                    ON venture_cost_events(opportunity_id, recorded_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_venture_costs_external_ref
                    ON venture_cost_events(source, external_ref)
                    WHERE external_ref IS NOT NULL;
                """
            )

    def save_cost(self, event: VentureCostEvent) -> VentureCostEvent:
        if event.external_ref:
            existing = self.get_by_external_ref(event.source, event.external_ref)
            if existing is not None:
                return existing
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO venture_cost_events "
                "(id, opportunity_id, amount_cents, currency, source, external_ref, payload, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(event.id),
                    str(event.opportunity_id),
                    event.amount_cents,
                    event.currency,
                    event.source,
                    event.external_ref,
                    event.model_dump_json(),
                    event.recorded_at.isoformat(),
                ),
            )
        return event

    def get_by_external_ref(self, source: str, external_ref: str) -> VentureCostEvent | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM venture_cost_events WHERE source = ? AND external_ref = ?",
                (source, external_ref),
            ).fetchone()
        return VentureCostEvent.model_validate_json(row["payload"]) if row else None

    def list_costs(self, opportunity_id: UUID) -> list[VentureCostEvent]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM venture_cost_events WHERE opportunity_id = ? "
                "ORDER BY recorded_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [VentureCostEvent.model_validate_json(row["payload"]) for row in rows]


class VentureValueEstimator:
    """Evidence-first economics estimator. It never converts currencies implicitly."""

    def __init__(self, engine: HelisEngine) -> None:
        self.engine = engine
        self.gtm = GTMStore(engine.store)
        self.economics = VentureEconomicsStore(engine)

    def record_cost(self, event: VentureCostEvent) -> VentureCostEvent:
        opportunity = self.engine.store.get_opportunity(event.opportunity_id)
        if opportunity is None:
            raise ValueError(f"opportunity not found: {event.opportunity_id}")
        saved = self.economics.save_cost(event)
        if saved.id == event.id:
            self.engine.store.append_event(
                AuditEvent(
                    event_type="portfolio.cost_recorded",
                    entity_id=event.id,
                    data={
                        "opportunity_id": str(event.opportunity_id),
                        "amount_cents": event.amount_cents,
                        "currency": event.currency,
                        "source": event.source,
                        "external_ref": event.external_ref,
                    },
                )
            )
        return saved

    def estimate(self, opportunity_id: UUID, currency: str) -> VentureValueEstimate:
        currency = currency.upper()
        responses = self.gtm.list_responses(opportunity_id)
        resolved = len(responses)
        paid_sales = [
            response
            for response in responses
            if response.kind == LeadResponseKind.SALE
            and response.currency.upper() == currency
            and response.revenue_cents > 0
        ]
        revenue = sum(
            event.amount_cents
            for event in self.gtm.list_revenue(opportunity_id)
            if event.currency.upper() == currency
        )
        costs = sum(
            event.amount_cents
            for event in self.economics.list_costs(opportunity_id)
            if event.currency.upper() == currency
        )

        # Conservative Beta(1, 9) prior: before data, paid-sale probability is 10%.
        posterior_sale_probability = (len(paid_sales) + 1) / (resolved + 10)
        average_sale_value = revenue / len(paid_sales) if paid_sales else 0.0
        expected_revenue = posterior_sale_probability * average_sale_value
        observed_cost_per_outcome = costs / resolved if resolved else 0.0
        expected_net = expected_revenue - observed_cost_per_outcome

        # Smoothly grows with evidence and never reaches artificial certainty.
        confidence = min(0.95, 1 - math.exp(-resolved / 8)) if resolved else 0.0
        uncertainty = 1 - confidence
        roi = (revenue - costs) / costs if costs > 0 else None
        return VentureValueEstimate(
            opportunity_id=opportunity_id,
            currency=currency,
            resolved_outcomes=resolved,
            paid_sales=len(paid_sales),
            observed_revenue_cents=revenue,
            observed_cost_cents=costs,
            realized_net_cents=revenue - costs,
            posterior_paid_sale_probability=round(posterior_sale_probability, 6),
            average_paid_sale_value_cents=round(average_sale_value, 4),
            expected_revenue_per_next_resolved_contact_cents=round(expected_revenue, 4),
            observed_cost_per_resolved_contact_cents=round(observed_cost_per_outcome, 4),
            expected_net_per_next_resolved_contact_cents=round(expected_net, 4),
            evidence_confidence=round(confidence, 6),
            uncertainty=round(uncertainty, 6),
            realized_roi=round(roi, 6) if roi is not None else None,
        )
