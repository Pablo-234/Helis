from __future__ import annotations

from uuid import UUID

from helis.agent_spec_domain import AgentSpecBundle
from helis.store import HelisStore


class AgentSpecStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_spec_bundles (
                    id TEXT PRIMARY KEY,
                    architecture_id TEXT UNIQUE NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    bundle_hash TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_specs_opportunity
                    ON agent_spec_bundles(opportunity_id, created_at);
                """
            )

    def save(self, bundle: AgentSpecBundle) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO agent_spec_bundles "
                "(id, architecture_id, opportunity_id, bundle_hash, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(bundle.id),
                    str(bundle.architecture_id),
                    str(bundle.opportunity_id),
                    bundle.bundle_hash,
                    bundle.model_dump_json(),
                    bundle.created_at.isoformat(),
                ),
            )

    def get_for_architecture(self, architecture_id: UUID) -> AgentSpecBundle | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM agent_spec_bundles WHERE architecture_id = ?",
                (str(architecture_id),),
            ).fetchone()
        return AgentSpecBundle.model_validate_json(row["payload"]) if row else None

    def latest(self, opportunity_id: UUID) -> AgentSpecBundle | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM agent_spec_bundles WHERE opportunity_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return AgentSpecBundle.model_validate_json(row["payload"]) if row else None

    def list(self, opportunity_id: UUID) -> list[AgentSpecBundle]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM agent_spec_bundles WHERE opportunity_id = ? "
                "ORDER BY created_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [AgentSpecBundle.model_validate_json(row["payload"]) for row in rows]
