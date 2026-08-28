from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

from helis.gtm_domain import (
    Lead,
    LeadResponse,
    OutreachDraft,
    OutreachRun,
    ProspectQuery,
    RevenueEvent,
)
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
                CREATE TABLE IF NOT EXISTS outreach_runs (
                    id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    lead_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_outreach_runs_draft
                    ON outreach_runs(draft_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_outreach_runs_lead
                    ON outreach_runs(lead_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_outreach_runs_venture
                    ON outreach_runs(opportunity_id, updated_at);
                CREATE TABLE IF NOT EXISTS lead_responses (
                    id TEXT PRIMARY KEY,
                    run_id TEXT UNIQUE NOT NULL,
                    lead_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_lead_responses_venture
                    ON lead_responses(opportunity_id, created_at);
                CREATE TABLE IF NOT EXISTS revenue_events (
                    id TEXT PRIMARY KEY,
                    response_id TEXT UNIQUE,
                    lead_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_revenue_events_venture
                    ON revenue_events(opportunity_id, recorded_at);
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

    def get_draft(self, draft_id: UUID) -> OutreachDraft | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM outreach_drafts WHERE id = ?", (str(draft_id),)
            ).fetchone()
        return OutreachDraft.model_validate_json(row["payload"]) if row else None

    def get_draft_for_lead(self, lead_id: UUID) -> OutreachDraft | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM outreach_drafts WHERE lead_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(lead_id),),
            ).fetchone()
        return OutreachDraft.model_validate_json(row["payload"]) if row else None

    def list_drafts(self, opportunity_id: UUID) -> list[OutreachDraft]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM outreach_drafts WHERE opportunity_id = ? ORDER BY created_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [OutreachDraft.model_validate_json(row["payload"]) for row in rows]

    def save_outreach_run(self, run: OutreachRun) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO outreach_runs "
                "(id, draft_id, lead_id, opportunity_id, status, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(run.id),
                    str(run.draft_id),
                    str(run.lead_id),
                    str(run.opportunity_id),
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                ),
            )

    def get_outreach_run(self, run_id: UUID) -> OutreachRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM outreach_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return OutreachRun.model_validate_json(row["payload"]) if row else None

    def get_latest_run_for_draft(self, draft_id: UUID) -> OutreachRun | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM outreach_runs WHERE draft_id = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (str(draft_id),),
            ).fetchone()
        return OutreachRun.model_validate_json(row["payload"]) if row else None

    def list_outreach_runs(self, opportunity_id: UUID | None = None) -> list[OutreachRun]:
        with self.store.connect() as db:
            if opportunity_id is None:
                rows = db.execute(
                    "SELECT payload FROM outreach_runs ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM outreach_runs WHERE opportunity_id = ? "
                    "ORDER BY updated_at DESC",
                    (str(opportunity_id),),
                ).fetchall()
        return [OutreachRun.model_validate_json(row["payload"]) for row in rows]

    def save_response(self, response: LeadResponse) -> bool:
        with self.store.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO lead_responses "
                "(id, run_id, lead_id, opportunity_id, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(response.id),
                    str(response.run_id),
                    str(response.lead_id),
                    str(response.opportunity_id),
                    response.model_dump_json(),
                    response.created_at.isoformat(),
                ),
            )
            return cursor.rowcount > 0

    def get_response_for_run(self, run_id: UUID) -> LeadResponse | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM lead_responses WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return LeadResponse.model_validate_json(row["payload"]) if row else None

    def list_responses(self, opportunity_id: UUID) -> list[LeadResponse]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM lead_responses WHERE opportunity_id = ? ORDER BY created_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [LeadResponse.model_validate_json(row["payload"]) for row in rows]

    def save_revenue(self, event: RevenueEvent) -> bool:
        with self.store.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO revenue_events "
                "(id, response_id, lead_id, opportunity_id, amount_cents, currency, payload, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(event.id),
                    str(event.response_id) if event.response_id else None,
                    str(event.lead_id),
                    str(event.opportunity_id),
                    event.amount_cents,
                    event.currency,
                    event.model_dump_json(),
                    event.recorded_at.isoformat(),
                ),
            )
            return cursor.rowcount > 0

    def get_revenue_for_response(self, response_id: UUID) -> RevenueEvent | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM revenue_events WHERE response_id = ?", (str(response_id),)
            ).fetchone()
        return RevenueEvent.model_validate_json(row["payload"]) if row else None

    def list_revenue(self, opportunity_id: UUID | None = None) -> list[RevenueEvent]:
        with self.store.connect() as db:
            if opportunity_id is None:
                rows = db.execute(
                    "SELECT payload FROM revenue_events ORDER BY recorded_at ASC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT payload FROM revenue_events WHERE opportunity_id = ? "
                    "ORDER BY recorded_at ASC",
                    (str(opportunity_id),),
                ).fetchall()
        return [RevenueEvent.model_validate_json(row["payload"]) for row in rows]

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
