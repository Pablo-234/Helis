from __future__ import annotations

from uuid import UUID

from helis.self_improvement_branch_domain import BranchMaterializationRun
from helis.store import HelisStore


class SelfImprovementBranchStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS self_improvement_branch_runs (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT UNIQUE NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_improvement_branch_runs_status
                    ON self_improvement_branch_runs(status, updated_at);
                """
            )

    def save(self, run: BranchMaterializationRun) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO self_improvement_branch_runs "
                "(id, proposal_id, candidate_id, candidate_hash, status, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(run.id),
                    str(run.proposal_id),
                    str(run.candidate_id),
                    run.candidate_hash,
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                ),
            )

    def get(self, run_id: UUID) -> BranchMaterializationRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM self_improvement_branch_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
        return BranchMaterializationRun.model_validate_json(row["payload"]) if row else None

    def get_for_proposal(self, proposal_id: UUID) -> BranchMaterializationRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM self_improvement_branch_runs WHERE proposal_id = ?",
                (str(proposal_id),),
            ).fetchone()
        return BranchMaterializationRun.model_validate_json(row["payload"]) if row else None

    def list(self) -> list[BranchMaterializationRun]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM self_improvement_branch_runs ORDER BY updated_at DESC"
            ).fetchall()
        return [BranchMaterializationRun.model_validate_json(row["payload"]) for row in rows]
