from __future__ import annotations

from uuid import UUID

from helis.build_domain import BuildRun, BuildSpec
from helis.store import HelisStore


class BuildStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS build_specs (
                    opportunity_id TEXT PRIMARY KEY,
                    spec_id TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS build_runs (
                    id TEXT PRIMARY KEY,
                    spec_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_build_runs_opportunity
                    ON build_runs(opportunity_id, updated_at);
                """
            )

    def save_spec(self, spec: BuildSpec) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO build_specs "
                "(opportunity_id, spec_id, payload, created_at) VALUES (?, ?, ?, ?)",
                (
                    str(spec.opportunity_id),
                    str(spec.id),
                    spec.model_dump_json(),
                    str(spec.created_at),
                ),
            )

    def get_spec(self, opportunity_id: UUID) -> BuildSpec | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM build_specs WHERE opportunity_id = ?",
                (str(opportunity_id),),
            ).fetchone()
        return BuildSpec.model_validate_json(row["payload"]) if row else None

    def save_run(self, run: BuildRun) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO build_runs "
                "(id, spec_id, opportunity_id, status, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(run.id),
                    str(run.spec_id),
                    str(run.opportunity_id),
                    run.status.value,
                    run.model_dump_json(),
                    str(run.updated_at),
                ),
            )

    def get_latest_run(self, opportunity_id: UUID) -> BuildRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM build_runs WHERE opportunity_id = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return BuildRun.model_validate_json(row["payload"]) if row else None

    def list_runs(self, opportunity_id: UUID | None = None) -> list[BuildRun]:
        with self.store.connect() as db:
            if opportunity_id is None:
                rows = db.execute(
                    "SELECT payload FROM build_runs ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM build_runs WHERE opportunity_id = ? "
                    "ORDER BY updated_at DESC",
                    (str(opportunity_id),),
                ).fetchall()
        return [BuildRun.model_validate_json(row["payload"]) for row in rows]
