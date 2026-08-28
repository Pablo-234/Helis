from __future__ import annotations

from uuid import UUID

from helis.commerce_domain import (
    CheckoutBinding,
    CheckoutRun,
    CommerceOffer,
    CommerceRevenueEvent,
)
from helis.store import HelisStore


class CommerceStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS commerce_offers (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    offer_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(opportunity_id, offer_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_commerce_offers_opportunity
                    ON commerce_offers(opportunity_id, created_at);
                CREATE TABLE IF NOT EXISTS checkout_runs (
                    id TEXT PRIMARY KEY,
                    offer_id TEXT UNIQUE NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkout_runs_opportunity
                    ON checkout_runs(opportunity_id, updated_at);
                CREATE TABLE IF NOT EXISTS checkout_bindings (
                    id TEXT PRIMARY KEY,
                    run_id TEXT UNIQUE NOT NULL,
                    offer_id TEXT UNIQUE NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkout_bindings_opportunity
                    ON checkout_bindings(opportunity_id, created_at);
                CREATE TABLE IF NOT EXISTS commerce_revenue_events (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    offer_id TEXT NOT NULL,
                    checkout_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    source TEXT NOT NULL,
                    external_ref TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(source, external_ref)
                );
                CREATE INDEX IF NOT EXISTS idx_commerce_revenue_opportunity
                    ON commerce_revenue_events(opportunity_id, recorded_at);
                """
            )

    def save_offer(self, offer: CommerceOffer) -> CommerceOffer:
        existing = self.get_offer_by_hash(offer.opportunity_id, offer.offer_hash)
        if existing is not None:
            return existing
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO commerce_offers "
                "(id, opportunity_id, offer_hash, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(offer.id),
                    str(offer.opportunity_id),
                    offer.offer_hash,
                    offer.model_dump_json(),
                    offer.created_at.isoformat(),
                ),
            )
        return offer

    def get_offer(self, offer_id: UUID) -> CommerceOffer | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM commerce_offers WHERE id = ?", (str(offer_id),)
            ).fetchone()
        return CommerceOffer.model_validate_json(row["payload"]) if row else None

    def get_offer_by_hash(self, opportunity_id: UUID, offer_hash: str) -> CommerceOffer | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM commerce_offers WHERE opportunity_id = ? AND offer_hash = ?",
                (str(opportunity_id), offer_hash),
            ).fetchone()
        return CommerceOffer.model_validate_json(row["payload"]) if row else None

    def latest_offer(self, opportunity_id: UUID) -> CommerceOffer | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM commerce_offers WHERE opportunity_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return CommerceOffer.model_validate_json(row["payload"]) if row else None

    def save_run(self, run: CheckoutRun) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO checkout_runs "
                "(id, offer_id, opportunity_id, status, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(run.id),
                    str(run.offer_id),
                    str(run.opportunity_id),
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                ),
            )

    def get_run(self, run_id: UUID) -> CheckoutRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM checkout_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return CheckoutRun.model_validate_json(row["payload"]) if row else None

    def get_run_for_offer(self, offer_id: UUID) -> CheckoutRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM checkout_runs WHERE offer_id = ?", (str(offer_id),)
            ).fetchone()
        return CheckoutRun.model_validate_json(row["payload"]) if row else None

    def list_runs(self, opportunity_id: UUID | None = None) -> list[CheckoutRun]:
        with self.store.connect() as db:
            if opportunity_id is None:
                rows = db.execute(
                    "SELECT payload FROM checkout_runs ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM checkout_runs WHERE opportunity_id = ? "
                    "ORDER BY updated_at DESC",
                    (str(opportunity_id),),
                ).fetchall()
        return [CheckoutRun.model_validate_json(row["payload"]) for row in rows]

    def save_binding(self, binding: CheckoutBinding) -> CheckoutBinding:
        existing = self.get_binding_for_run(binding.run_id)
        if existing is not None:
            return existing
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO checkout_bindings "
                "(id, run_id, offer_id, opportunity_id, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(binding.id),
                    str(binding.run_id),
                    str(binding.offer_id),
                    str(binding.opportunity_id),
                    binding.model_dump_json(),
                    binding.created_at.isoformat(),
                ),
            )
        return binding

    def get_binding_for_run(self, run_id: UUID) -> CheckoutBinding | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM checkout_bindings WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return CheckoutBinding.model_validate_json(row["payload"]) if row else None

    def get_binding_for_offer(self, offer_id: UUID) -> CheckoutBinding | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM checkout_bindings WHERE offer_id = ?", (str(offer_id),)
            ).fetchone()
        return CheckoutBinding.model_validate_json(row["payload"]) if row else None

    def latest_binding(self, opportunity_id: UUID) -> CheckoutBinding | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM checkout_bindings WHERE opportunity_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return CheckoutBinding.model_validate_json(row["payload"]) if row else None

    def save_revenue(self, event: CommerceRevenueEvent) -> CommerceRevenueEvent:
        existing = self.get_revenue_by_external_ref(event.source, event.external_ref)
        if existing is not None:
            return existing
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO commerce_revenue_events "
                "(id, opportunity_id, offer_id, checkout_id, amount_cents, currency, "
                "source, external_ref, payload, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(event.id),
                    str(event.opportunity_id),
                    str(event.offer_id),
                    str(event.checkout_id),
                    event.amount_cents,
                    event.currency,
                    event.source,
                    event.external_ref,
                    event.model_dump_json(),
                    event.recorded_at.isoformat(),
                ),
            )
        return event

    def get_revenue_by_external_ref(
        self, source: str, external_ref: str
    ) -> CommerceRevenueEvent | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM commerce_revenue_events WHERE source = ? AND external_ref = ?",
                (source, external_ref),
            ).fetchone()
        return CommerceRevenueEvent.model_validate_json(row["payload"]) if row else None

    def list_revenue(self, opportunity_id: UUID | None = None) -> list[CommerceRevenueEvent]:
        with self.store.connect() as db:
            if opportunity_id is None:
                rows = db.execute(
                    "SELECT payload FROM commerce_revenue_events ORDER BY recorded_at ASC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM commerce_revenue_events WHERE opportunity_id = ? "
                    "ORDER BY recorded_at ASC",
                    (str(opportunity_id),),
                ).fetchall()
        return [CommerceRevenueEvent.model_validate_json(row["payload"]) for row in rows]
