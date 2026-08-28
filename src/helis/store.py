from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from helis.domain import (
    AuditEvent,
    BuildCheck,
    BuildReview,
    BuildRun,
    BuildSpec,
    Experiment,
    ExperimentRun,
    Observation,
    Opportunity,
    PreviewManifest,
    Scorecard,
    SkepticReport,
    ValidationResult,
    VentureDecision,
    utc_now,
)


class HelisStore:
    def __init__(self, path: str | Path = "helis.db") -> None:
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_observations (
                    observation_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS opportunities (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scorecards (
                    opportunity_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    scored_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skeptic_reports (
                    opportunity_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_runs (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_experiment_runs_experiment
                    ON experiment_runs(experiment_id, updated_at);
                CREATE TABLE IF NOT EXISTS validation_results (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_validation_results_opportunity
                    ON validation_results(opportunity_id, created_at);
                CREATE TABLE IF NOT EXISTS venture_decisions (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    decided_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_venture_decisions_opportunity
                    ON venture_decisions(opportunity_id, decided_at);
                CREATE TABLE IF NOT EXISTS build_specs (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_build_specs_opportunity
                    ON build_specs(opportunity_id, created_at);
                CREATE TABLE IF NOT EXISTS build_runs (
                    id TEXT PRIMARY KEY,
                    spec_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_build_runs_spec
                    ON build_runs(spec_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_build_runs_opportunity
                    ON build_runs(opportunity_id, updated_at);
                CREATE TABLE IF NOT EXISTS build_checks (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_build_checks_run
                    ON build_checks(run_id, created_at);
                CREATE TABLE IF NOT EXISTS build_reviews (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_build_reviews_run
                    ON build_reviews(run_id, created_at);
                CREATE TABLE IF NOT EXISTS preview_manifests (
                    id TEXT PRIMARY KEY,
                    run_id TEXT UNIQUE NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_preview_opportunity
                    ON preview_manifests(opportunity_id, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL,
                    event_type TEXT NOT NULL,
                    entity_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def append_event(self, event: AuditEvent) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO events (id, event_type, entity_id, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(event.id),
                    event.event_type,
                    str(event.entity_id) if event.entity_id else None,
                    event.model_dump_json(),
                    event.created_at.isoformat(),
                ),
            )

    def save_observation(self, observation: Observation) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO observations (id, payload, captured_at) VALUES (?, ?, ?)",
                (str(observation.id), observation.model_dump_json(), observation.captured_at.isoformat()),
            )
            return cursor.rowcount > 0

    def list_observations(self, limit: int = 1000) -> list[Observation]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM observations ORDER BY captured_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Observation.model_validate_json(row["payload"]) for row in rows]

    def list_unprocessed_observations(self, limit: int = 1000) -> list[Observation]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT observations.payload
                FROM observations
                LEFT JOIN processed_observations
                    ON processed_observations.observation_id = observations.id
                WHERE processed_observations.observation_id IS NULL
                ORDER BY observations.captured_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [Observation.model_validate_json(row["payload"]) for row in rows]

    def mark_observations_processed(self, observation_ids: Iterable[UUID]) -> None:
        processed_at = utc_now().isoformat()
        rows = [(str(observation_id), processed_at) for observation_id in observation_ids]
        if not rows:
            return
        with self.connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO processed_observations (observation_id, processed_at) VALUES (?, ?)",
                rows,
            )

    def save_opportunity(self, opportunity: Opportunity) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO opportunities (id, payload, created_at) VALUES (?, ?, ?)",
                (str(opportunity.id), opportunity.model_dump_json(), opportunity.discovered_at.isoformat()),
            )

    def list_opportunities(self) -> list[Opportunity]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM opportunities ORDER BY created_at DESC").fetchall()
        return [Opportunity.model_validate_json(row["payload"]) for row in rows]

    def get_opportunity(self, opportunity_id: UUID) -> Opportunity | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM opportunities WHERE id = ?", (str(opportunity_id),)
            ).fetchone()
        return Opportunity.model_validate_json(row["payload"]) if row else None

    def save_scorecard(self, scorecard: Scorecard) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO scorecards (opportunity_id, payload, scored_at) VALUES (?, ?, ?)",
                (
                    str(scorecard.opportunity_id),
                    scorecard.model_dump_json(),
                    scorecard.scored_at.isoformat(),
                ),
            )

    def list_scorecards(self) -> list[Scorecard]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM scorecards").fetchall()
        cards = [Scorecard.model_validate_json(row["payload"]) for row in rows]
        return sorted(cards, key=lambda card: card.total, reverse=True)

    def save_skeptic_report(self, report: SkepticReport) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO skeptic_reports (opportunity_id, payload, created_at) VALUES (?, ?, ?)",
                (str(report.opportunity_id), report.model_dump_json(), report.created_at.isoformat()),
            )

    def get_skeptic_report(self, opportunity_id: UUID) -> SkepticReport | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM skeptic_reports WHERE opportunity_id = ?",
                (str(opportunity_id),),
            ).fetchone()
        return SkepticReport.model_validate_json(row["payload"]) if row else None

    def save_experiment(self, experiment: Experiment) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO experiments (id, opportunity_id, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    str(experiment.id),
                    str(experiment.opportunity_id),
                    experiment.model_dump_json(),
                    experiment.created_at.isoformat(),
                ),
            )

    def get_experiment(self, experiment_id: UUID) -> Experiment | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM experiments WHERE id = ?", (str(experiment_id),)
            ).fetchone()
        return Experiment.model_validate_json(row["payload"]) if row else None

    def list_experiments(self, opportunity_id: UUID | None = None) -> list[Experiment]:
        with self.connect() as db:
            if opportunity_id is None:
                rows = db.execute("SELECT payload FROM experiments ORDER BY created_at DESC").fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM experiments WHERE opportunity_id = ? ORDER BY created_at DESC",
                    (str(opportunity_id),),
                ).fetchall()
        return [Experiment.model_validate_json(row["payload"]) for row in rows]

    def save_experiment_run(self, run: ExperimentRun) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO experiment_runs "
                "(id, experiment_id, opportunity_id, status, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(run.id),
                    str(run.experiment_id),
                    str(run.opportunity_id),
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                ),
            )

    def get_experiment_run(self, run_id: UUID) -> ExperimentRun | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM experiment_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return ExperimentRun.model_validate_json(row["payload"]) if row else None

    def list_experiment_runs(
        self,
        *,
        opportunity_id: UUID | None = None,
        experiment_id: UUID | None = None,
    ) -> list[ExperimentRun]:
        clauses: list[str] = []
        params: list[str] = []
        if opportunity_id is not None:
            clauses.append("opportunity_id = ?")
            params.append(str(opportunity_id))
        if experiment_id is not None:
            clauses.append("experiment_id = ?")
            params.append(str(experiment_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = "SELECT payload FROM experiment_runs" + where + " ORDER BY updated_at DESC"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [ExperimentRun.model_validate_json(row["payload"]) for row in rows]

    def save_validation_result(self, result: ValidationResult) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO validation_results "
                "(id, run_id, experiment_id, opportunity_id, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(result.id),
                    str(result.run_id),
                    str(result.experiment_id),
                    str(result.opportunity_id),
                    result.model_dump_json(),
                    result.created_at.isoformat(),
                ),
            )

    def list_validation_results(self, opportunity_id: UUID | None = None) -> list[ValidationResult]:
        with self.connect() as db:
            if opportunity_id is None:
                rows = db.execute(
                    "SELECT payload FROM validation_results ORDER BY created_at ASC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM validation_results WHERE opportunity_id = ? "
                    "ORDER BY created_at ASC",
                    (str(opportunity_id),),
                ).fetchall()
        return [ValidationResult.model_validate_json(row["payload"]) for row in rows]

    def save_venture_decision(self, decision: VentureDecision) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO venture_decisions "
                "(id, opportunity_id, decision, payload, decided_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(decision.id),
                    str(decision.opportunity_id),
                    decision.decision.value,
                    decision.model_dump_json(),
                    decision.decided_at.isoformat(),
                ),
            )

    def list_venture_decisions(self, opportunity_id: UUID | None = None) -> list[VentureDecision]:
        with self.connect() as db:
            if opportunity_id is None:
                rows = db.execute(
                    "SELECT payload FROM venture_decisions ORDER BY decided_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM venture_decisions WHERE opportunity_id = ? "
                    "ORDER BY decided_at DESC",
                    (str(opportunity_id),),
                ).fetchall()
        return [VentureDecision.model_validate_json(row["payload"]) for row in rows]

    def save_build_spec(self, spec: BuildSpec) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO build_specs (id, opportunity_id, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    str(spec.id),
                    str(spec.opportunity_id),
                    spec.model_dump_json(),
                    spec.created_at.isoformat(),
                ),
            )

    def get_build_spec_for_opportunity(self, opportunity_id: UUID) -> BuildSpec | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM build_specs WHERE opportunity_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return BuildSpec.model_validate_json(row["payload"]) if row else None

    def save_build_run(self, run: BuildRun) -> None:
        with self.connect() as db:
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
                    run.updated_at.isoformat(),
                ),
            )

    def get_build_run(self, run_id: UUID) -> BuildRun | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM build_runs WHERE id = ?", (str(run_id),)).fetchone()
        return BuildRun.model_validate_json(row["payload"]) if row else None

    def list_build_runs(
        self,
        *,
        spec_id: UUID | None = None,
        opportunity_id: UUID | None = None,
    ) -> list[BuildRun]:
        clauses: list[str] = []
        params: list[str] = []
        if spec_id is not None:
            clauses.append("spec_id = ?")
            params.append(str(spec_id))
        if opportunity_id is not None:
            clauses.append("opportunity_id = ?")
            params.append(str(opportunity_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = "SELECT payload FROM build_runs" + where + " ORDER BY updated_at DESC"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [BuildRun.model_validate_json(row["payload"]) for row in rows]

    def save_build_check(self, check: BuildCheck) -> None:
        if check.run_id is None:
            raise ValueError("build check requires run_id before persistence")
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO build_checks (id, run_id, passed, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(check.id),
                    str(check.run_id),
                    int(check.passed),
                    check.model_dump_json(),
                    check.created_at.isoformat(),
                ),
            )

    def list_build_checks(self, run_id: UUID) -> list[BuildCheck]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM build_checks WHERE run_id = ? ORDER BY created_at ASC",
                (str(run_id),),
            ).fetchall()
        return [BuildCheck.model_validate_json(row["payload"]) for row in rows]

    def save_build_review(self, review: BuildReview) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO build_reviews (id, run_id, verdict, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(review.id),
                    str(review.run_id),
                    review.verdict.value,
                    review.model_dump_json(),
                    review.created_at.isoformat(),
                ),
            )

    def get_build_review(self, run_id: UUID) -> BuildReview | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM build_reviews WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (str(run_id),),
            ).fetchone()
        return BuildReview.model_validate_json(row["payload"]) if row else None

    def save_preview_manifest(self, preview: PreviewManifest) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO preview_manifests "
                "(id, run_id, opportunity_id, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(preview.id),
                    str(preview.run_id),
                    str(preview.opportunity_id),
                    preview.model_dump_json(),
                    preview.created_at.isoformat(),
                ),
            )

    def get_preview_manifest_for_opportunity(
        self,
        opportunity_id: UUID,
    ) -> PreviewManifest | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM preview_manifests WHERE opportunity_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return PreviewManifest.model_validate_json(row["payload"]) if row else None

    def list_events(self, limit: int = 100) -> list[AuditEvent]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM events ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [AuditEvent.model_validate_json(row["payload"]) for row in rows]
