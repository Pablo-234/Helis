from __future__ import annotations

from uuid import UUID

from helis.child_agent_domain import ChildAgentArtifact
from helis.store import HelisStore


class ChildAgentArtifactStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS child_agent_artifacts (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    spec_id TEXT UNIQUE NOT NULL,
                    artifact_hash TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_child_agents_opportunity
                    ON child_agent_artifacts(opportunity_id, created_at);
                """
            )

    def save(self, artifact: ChildAgentArtifact) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO child_agent_artifacts "
                "(id, opportunity_id, spec_id, artifact_hash, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(artifact.id),
                    str(artifact.opportunity_id),
                    str(artifact.spec_id),
                    artifact.artifact_hash,
                    artifact.model_dump_json(),
                    artifact.created_at.isoformat(),
                ),
            )

    def get(self, artifact_id: UUID) -> ChildAgentArtifact | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM child_agent_artifacts WHERE id = ?",
                (str(artifact_id),),
            ).fetchone()
        return ChildAgentArtifact.model_validate_json(row["payload"]) if row else None

    def get_for_spec(self, spec_id: UUID) -> ChildAgentArtifact | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM child_agent_artifacts WHERE spec_id = ?",
                (str(spec_id),),
            ).fetchone()
        return ChildAgentArtifact.model_validate_json(row["payload"]) if row else None

    def list(self, opportunity_id: UUID) -> list[ChildAgentArtifact]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM child_agent_artifacts WHERE opportunity_id = ? "
                "ORDER BY created_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [ChildAgentArtifact.model_validate_json(row["payload"]) for row in rows]
