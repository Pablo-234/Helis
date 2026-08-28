from __future__ import annotations

from uuid import UUID

from helis.gtm_experiment_domain import GTMExperiment, GTMExperimentStatus
from helis.store import HelisStore


class GTMExperimentStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS gtm_experiments (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_gtm_experiments_venture_status
                    ON gtm_experiments(opportunity_id, status, updated_at);
                """
            )

    def save(self, experiment: GTMExperiment) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO gtm_experiments "
                "(id, opportunity_id, status, payload, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(experiment.id),
                    str(experiment.opportunity_id),
                    experiment.status.value,
                    experiment.model_dump_json(),
                    experiment.updated_at.isoformat(),
                ),
            )

    def get(self, experiment_id: UUID) -> GTMExperiment | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM gtm_experiments WHERE id = ?",
                (str(experiment_id),),
            ).fetchone()
        return GTMExperiment.model_validate_json(row["payload"]) if row else None

    def list(self, opportunity_id: UUID) -> list[GTMExperiment]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM gtm_experiments WHERE opportunity_id = ? "
                "ORDER BY updated_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [GTMExperiment.model_validate_json(row["payload"]) for row in rows]

    def active(self, opportunity_id: UUID) -> GTMExperiment | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM gtm_experiments WHERE opportunity_id = ? AND status = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (str(opportunity_id), GTMExperimentStatus.ACTIVE.value),
            ).fetchone()
        return GTMExperiment.model_validate_json(row["payload"]) if row else None

    def latest(self, opportunity_id: UUID) -> GTMExperiment | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM gtm_experiments WHERE opportunity_id = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return GTMExperiment.model_validate_json(row["payload"]) if row else None
