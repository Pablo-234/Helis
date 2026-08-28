from __future__ import annotations

from uuid import UUID

from helis.first_transaction_domain import FirstTransactionPlan
from helis.store import HelisStore


class FirstTransactionStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS first_transaction_plans (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(opportunity_id, input_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_first_transaction_opportunity
                    ON first_transaction_plans(opportunity_id, created_at);
                """
            )

    def save(self, plan: FirstTransactionPlan) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO first_transaction_plans "
                "(id, opportunity_id, input_hash, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(plan.id),
                    str(plan.opportunity_id),
                    plan.input_hash,
                    plan.model_dump_json(),
                    plan.created_at.isoformat(),
                ),
            )

    def get_for_snapshot(self, opportunity_id: UUID, input_hash: str) -> FirstTransactionPlan | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM first_transaction_plans "
                "WHERE opportunity_id = ? AND input_hash = ? LIMIT 1",
                (str(opportunity_id), input_hash),
            ).fetchone()
        return FirstTransactionPlan.model_validate_json(row["payload"]) if row else None

    def latest(self, opportunity_id: UUID) -> FirstTransactionPlan | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM first_transaction_plans WHERE opportunity_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return FirstTransactionPlan.model_validate_json(row["payload"]) if row else None

    def list(self, opportunity_id: UUID) -> list[FirstTransactionPlan]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM first_transaction_plans WHERE opportunity_id = ? "
                "ORDER BY created_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [FirstTransactionPlan.model_validate_json(row["payload"]) for row in rows]
