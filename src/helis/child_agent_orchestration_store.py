from __future__ import annotations

from uuid import UUID

from helis.child_agent_orchestration_domain import ChildAgentOrchestrationRun
from helis.store import HelisStore


class ChildAgentOrchestrationStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS child_agent_orchestrations (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    source_key TEXT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(opportunity_id, source_key)
                );
                CREATE INDEX IF NOT EXISTS idx_child_agent_orchestrations_venture
                    ON child_agent_orchestrations(opportunity_id, updated_at);
                """
            )

    def create(self, run: ChildAgentOrchestrationRun) -> ChildAgentOrchestrationRun:
        with self.store.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO child_agent_orchestrations "
                "(id, opportunity_id, source_key, status, payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(run.id),
                    str(run.opportunity_id),
                    run.source_key,
                    run.status.value,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            inserted = cursor.rowcount == 1
        if inserted:
            return run
        if run.source_key is not None:
            existing = self.get_for_source(run.opportunity_id, run.source_key)
            if existing is not None:
                return existing
        raise ValueError(f"child-agent orchestration id already exists: {run.id}")

    def save(self, run: ChildAgentOrchestrationRun) -> None:
        with self.store.connect() as db:
            cursor = db.execute(
                "UPDATE child_agent_orchestrations "
                "SET status = ?, payload = ?, updated_at = ? "
                "WHERE id = ? AND opportunity_id = ?",
                (
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                    str(run.id),
                    str(run.opportunity_id),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"child-agent orchestration not found: {run.id}")

    def save_if_unchanged(
        self,
        run: ChildAgentOrchestrationRun,
        *,
        expected_updated_at: str,
    ) -> bool:
        """Compare-and-swap one orchestration payload for a single-worker step claim."""
        with self.store.connect() as db:
            cursor = db.execute(
                "UPDATE child_agent_orchestrations "
                "SET status = ?, payload = ?, updated_at = ? "
                "WHERE id = ? AND opportunity_id = ? AND updated_at = ?",
                (
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                    str(run.id),
                    str(run.opportunity_id),
                    expected_updated_at,
                ),
            )
            return cursor.rowcount == 1

    def get(self, run_id: UUID) -> ChildAgentOrchestrationRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM child_agent_orchestrations WHERE id = ?",
                (str(run_id),),
            ).fetchone()
        return ChildAgentOrchestrationRun.model_validate_json(row["payload"]) if row else None

    def get_for_source(
        self,
        opportunity_id: UUID,
        source_key: str,
    ) -> ChildAgentOrchestrationRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM child_agent_orchestrations "
                "WHERE opportunity_id = ? AND source_key = ?",
                (str(opportunity_id), source_key),
            ).fetchone()
        return ChildAgentOrchestrationRun.model_validate_json(row["payload"]) if row else None

    def list(self, opportunity_id: UUID) -> list[ChildAgentOrchestrationRun]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM child_agent_orchestrations "
                "WHERE opportunity_id = ? ORDER BY created_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [ChildAgentOrchestrationRun.model_validate_json(row["payload"]) for row in rows]

    def list_all(self) -> list[ChildAgentOrchestrationRun]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM child_agent_orchestrations ORDER BY updated_at DESC"
            ).fetchall()
        return [ChildAgentOrchestrationRun.model_validate_json(row["payload"]) for row in rows]
