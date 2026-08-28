from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

from helis.gtm_domain import Lead, OutreachDraft, ProspectQuery
from helis.store import HelisStore


def lead_identity(lead: Lead) -> str:
    for value in (lead.website, lead.contact_endpoint):
        if not value:
            continue
        parsed = urlsplit(value if "://" in value else f"https://{value}")
        if parsed.hostname:
            return parsed.hostname.lower().removeprefix("www.")
    return " ".join(lead.organization.lower().split())


class GTMStore:
    def __init__(self, store: HelisStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS prospect_queries (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_prospect_queries_venture
                    ON prospect_queries(opportunity_id, created_at);
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    identity_key TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(opportunity_id, identity_key)
                );
                CREATE INDEX IF NOT EXISTS idx_leads_venture_stage
                    ON leads(opportunity_id, stage, created_at);
                CREATE TABLE IF NOT EXISTS outreach_drafts (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_outreach_drafts_lead
                    ON outreach_drafts(lead_id, created_at);
                CREATE TABLE IF NOT EXISTS gtm_suppressions (
                    identity_key TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def save_query(self, query: ProspectQuery) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO prospect_queries (id, opportunity_id, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    str(query.id),
                    str(query.opportunity_id),
                    query.model_dump_json(),
                    query.created_at.isoformat(),
                ),
            )

    def list_queries(self, opportunity_id: UUID) -> list[ProspectQuery]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM prospect_queries WHERE opportunity_id = ? ORDER BY created_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [ProspectQuery.model_validate_json(row["payload"]) for row in rows]

    def save_lead(self, lead: Lead) -> bool:
        identity = lead_identity(lead)
        if self.is_suppressed(identity):
            return False
        with self.store.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO leads "
                "(id, opportunity_id, identity_key, stage, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(lead.id),
                    str(lead.opportunity_id),
                    identity,
                    lead.stage.value,
                    lead.model_dump_json(),
                    lead.created_at.isoformat(),
                ),
            )
            return cursor.rowcount > 0

    def update_lead(self, lead: Lead) -> None:
        with self.store.connect() as db:
            db.execute(
                "UPDATE leads SET stage = ?, payload = ? WHERE id = ?",
                (lead.stage.value, lead.model_dump_json(), str(lead.id)),
            )

    def get_lead(self, lead_id: UUID) -> Lead | None:
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM leads WHERE id = ?", (str(lead_id),)).fetchone()
        return Lead.model_validate_json(row["payload"]) if row else None

    def list_leads(self, opportunity_id: UUID) -> list[Lead]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM leads WHERE opportunity_id = ? ORDER BY created_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [Lead.model_validate_json(row["payload"]) for row in rows]

    def save_draft(self, draft: OutreachDraft) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO outreach_drafts "
                "(id, lead_id, opportunity_id, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(draft.id),
                    str(draft.lead_id),
                    str(draft.opportunity_id),
                    draft.model_dump_json(),
                    draft.created_at.isoformat(),
                ),
            )

    def get_draft_for_lead(self, lead_id: UUID) -> OutreachDraft | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM outreach_drafts WHERE lead_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(lead_id),),
            ).fetchone()
        return OutreachDraft.model_validate_json(row["payload"]) if row else None

    def suppress(self, identity_key: str, reason: str) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO gtm_suppressions (identity_key, reason) VALUES (?, ?)",
                (identity_key.lower().strip(), reason),
            )

    def is_suppressed(self, identity_key: str) -> bool:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT 1 FROM gtm_suppressions WHERE identity_key = ?",
                (identity_key.lower().strip(),),
            ).fetchone()
        return row is not None
