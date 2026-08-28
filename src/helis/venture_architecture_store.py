from __future__ import annotations

from uuid import UUID

from helis.store import HelisStore
from helis.venture_architecture_domain import VentureArchitecture


class VentureArchitectureStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS venture_architectures (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(opportunity_id, input_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_venture_architectures_opportunity
                    ON venture_architectures(opportunity_id, created_at);
                """
            )

    def save(self, architecture: VentureArchitecture) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO venture_architectures "
                "(id, opportunity_id, input_hash, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(architecture.id),
                    str(architecture.opportunity_id),
                    architecture.input_hash,
                    architecture.model_dump_json(),
                    architecture.created_at.isoformat(),
                ),
            )

    def get_for_snapshot(
        self,
        opportunity_id: UUID,
        input_hash: str,
    ) -> VentureArchitecture | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM venture_architectures "
                "WHERE opportunity_id = ? AND input_hash = ? LIMIT 1",
                (str(opportunity_id), input_hash),
            ).fetchone()
        return VentureArchitecture.model_validate_json(row["payload"]) if row else None

    def latest(self, opportunity_id: UUID) -> VentureArchitecture | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM venture_architectures WHERE opportunity_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return VentureArchitecture.model_validate_json(row["payload"]) if row else None

    def list(self, opportunity_id: UUID) -> list[VentureArchitecture]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM venture_architectures WHERE opportunity_id = ? "
                "ORDER BY created_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [VentureArchitecture.model_validate_json(row["payload"]) for row in rows]
