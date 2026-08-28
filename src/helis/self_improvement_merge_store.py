from __future__ import annotations

from uuid import UUID

from helis.self_improvement_merge_domain import SelfImprovementMergeRun
from helis.store import HelisStore


class SelfImprovementMergeStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS self_improvement_merge_runs (
                    id TEXT PRIMARY KEY,
                    branch_run_id TEXT UNIQUE NOT NULL,
                    proposal_id TEXT NOT NULL,
                    candidate_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_self_improvement_merge_runs_status
                    ON self_improvement_merge_runs(status, updated_at);
                """
            )

    def save(self, run: SelfImprovementMergeRun) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO self_improvement_merge_runs "
                "(id, branch_run_id, proposal_id, candidate_hash, status, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(run.id),
                    str(run.branch_run_id),
                    str(run.proposal_id),
                    run.candidate_hash,
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                ),
            )

    def get(self, run_id: UUID) -> SelfImprovementMergeRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM self_improvement_merge_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
        return SelfImprovementMergeRun.model_validate_json(row["payload"]) if row else None

    def get_for_branch_run(self, branch_run_id: UUID) -> SelfImprovementMergeRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM self_improvement_merge_runs WHERE branch_run_id = ?",
                (str(branch_run_id),),
            ).fetchone()
        return SelfImprovementMergeRun.model_validate_json(row["payload"]) if row else None

    def list(self) -> list[SelfImprovementMergeRun]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM self_improvement_merge_runs ORDER BY updated_at DESC"
            ).fetchall()
        return [SelfImprovementMergeRun.model_validate_json(row["payload"]) for row in rows]
