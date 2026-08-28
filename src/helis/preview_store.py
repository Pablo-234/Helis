from __future__ import annotations

from uuid import UUID

from helis.preview_domain import PreviewPublishRun, PublishedPreview
from helis.store import HelisStore


class PreviewPublicationStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS preview_publish_runs (
                    id TEXT PRIMARY KEY,
                    preview_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_preview_publish_preview
                    ON preview_publish_runs(preview_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_preview_publish_opportunity
                    ON preview_publish_runs(opportunity_id, updated_at);
                CREATE TABLE IF NOT EXISTS published_previews (
                    id TEXT PRIMARY KEY,
                    run_id TEXT UNIQUE NOT NULL,
                    preview_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    published_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_published_preview_opportunity
                    ON published_previews(opportunity_id, published_at);
                """
            )

    def save_run(self, run: PreviewPublishRun) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO preview_publish_runs "
                "(id, preview_id, opportunity_id, status, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(run.id),
                    str(run.preview_id),
                    str(run.opportunity_id),
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                ),
            )

    def get_run(self, run_id: UUID) -> PreviewPublishRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM preview_publish_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return PreviewPublishRun.model_validate_json(row["payload"]) if row else None

    def get_latest_for_preview(self, preview_id: UUID) -> PreviewPublishRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM preview_publish_runs WHERE preview_id = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (str(preview_id),),
            ).fetchone()
        return PreviewPublishRun.model_validate_json(row["payload"]) if row else None

    def list_runs(self, opportunity_id: UUID | None = None) -> list[PreviewPublishRun]:
        with self.store.connect() as db:
            if opportunity_id is None:
                rows = db.execute(
                    "SELECT payload FROM preview_publish_runs ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM preview_publish_runs WHERE opportunity_id = ? "
                    "ORDER BY updated_at DESC",
                    (str(opportunity_id),),
                ).fetchall()
        return [PreviewPublishRun.model_validate_json(row["payload"]) for row in rows]

    def save_publication(self, publication: PublishedPreview) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO published_previews "
                "(id, run_id, preview_id, opportunity_id, payload, published_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(publication.id),
                    str(publication.run_id),
                    str(publication.preview_id),
                    str(publication.opportunity_id),
                    publication.model_dump_json(),
                    publication.published_at.isoformat(),
                ),
            )

    def get_publication_for_run(self, run_id: UUID) -> PublishedPreview | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM published_previews WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return PublishedPreview.model_validate_json(row["payload"]) if row else None
