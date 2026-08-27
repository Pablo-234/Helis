from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from helis.domain import (
    AuditEvent,
    Experiment,
    Observation,
    Opportunity,
    Scorecard,
    SkepticReport,
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

    def list_events(self, limit: int = 100) -> list[AuditEvent]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM events ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [AuditEvent.model_validate_json(row["payload"]) for row in rows]
