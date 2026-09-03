from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APPROVAL_TABLES = {
    "experiment_runs": "Walidacja",
    "preview_publish_runs": "Publikacja MVP",
    "outreach_runs": "Pierwszy kontakt",
    "checkout_runs": "Uruchomienie płatności",
    "self_improvement_branch_runs": "Gałąź samodoskonalenia",
    "self_improvement_merge_runs": "Scalenie samodoskonalenia",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    return decoded if isinstance(decoded, dict) else {}


class DashboardSnapshotBuilder:
    """Build a credential-free owner view from a read-only HELIS database."""

    def __init__(
        self,
        db: str | Path = "helis.db",
        workspace_root: str | Path = ".helis/workspaces",
    ) -> None:
        self.db = Path(db).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve()

    def build(self) -> dict[str, Any]:
        if not self.db.is_file():
            return self._empty("Baza HELIS jeszcze nie istnieje")
        try:
            uri = f"{self.db.as_uri()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                return self._from_db(connection)
        except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            return self._empty(f"Nie można odczytać bazy: {type(exc).__name__}: {exc}")

    def _from_db(self, db: sqlite3.Connection) -> dict[str, Any]:
        tables = {
            str(row["name"])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        opportunities = self._payloads(db, tables, "opportunities", "created_at DESC")
        scorecards = {
            item.get("opportunity_id"): item
            for item in self._payloads(db, tables, "scorecards", "scored_at DESC")
        }
        experiments = self._group(
            self._payloads(db, tables, "experiments", "created_at DESC"),
            "opportunity_id",
        )
        experiment_runs = self._group(
            self._payloads(db, tables, "experiment_runs", "updated_at DESC"),
            "opportunity_id",
        )
        validation_results = self._group(
            self._payloads(db, tables, "validation_results", "created_at DESC"),
            "opportunity_id",
        )
        build_runs = self._group(
            self._payloads(db, tables, "build_runs", "updated_at DESC"),
            "opportunity_id",
        )
        previews = self._group(
            self._payloads(db, tables, "preview_manifests", "created_at DESC"),
            "opportunity_id",
        )
        leads = self._group(
            self._payloads(db, tables, "leads", "created_at DESC"),
            "opportunity_id",
        )
        outreach = self._group(
            self._payloads(db, tables, "outreach_runs", "updated_at DESC"),
            "opportunity_id",
        )
        revenue = self._group(
            [
                *self._payloads(db, tables, "revenue_events", "recorded_at DESC"),
                *self._payloads(db, tables, "commerce_revenue_events", "recorded_at DESC"),
            ],
            "opportunity_id",
        )
        approvals = self._approvals(db, tables)
        ventures = [
            self._venture(
                opportunity,
                scorecards.get(opportunity.get("id")),
                experiments.get(opportunity.get("id"), []),
                experiment_runs.get(opportunity.get("id"), []),
                validation_results.get(opportunity.get("id"), []),
                build_runs.get(opportunity.get("id"), []),
                previews.get(opportunity.get("id"), []),
                leads.get(opportunity.get("id"), []),
                outreach.get(opportunity.get("id"), []),
                revenue.get(opportunity.get("id"), []),
            )
            for opportunity in opportunities
        ]
        ventures.sort(key=lambda item: (item["score"] is not None, item["score"] or 0), reverse=True)
        stages = Counter(item["stage"] for item in ventures)
        discovery = self._latest(db, tables, "discovery_wake_results", "created_at")
        scheduler = self._latest(db, tables, "scheduler_wake_results", "created_at")
        return {
            "generated_at": _now(),
            "database": str(self.db),
            "status": "ok",
            "message": self._headline(ventures, approvals, discovery, scheduler),
            "summary": {
                "observations": self._count(db, tables, "observations"),
                "opportunities": len(ventures),
                "active_ventures": sum(item["stage"] not in {"killed", "paused"} for item in ventures),
                "builds": self._count(db, tables, "build_runs"),
                "leads": self._count(db, tables, "leads"),
                "pending_approvals": len(approvals),
            },
            "stages": dict(stages),
            "discovery": discovery,
            "scheduler": scheduler,
            "ventures": ventures,
            "approvals": approvals,
            "activity": self._activity(db, tables),
            "workspace": self._workspace_files(),
        }

    def _venture(
        self,
        opportunity: dict[str, Any],
        scorecard: dict[str, Any] | None,
        experiments: list[dict[str, Any]],
        experiment_runs: list[dict[str, Any]],
        validation_results: list[dict[str, Any]],
        build_runs: list[dict[str, Any]],
        previews: list[dict[str, Any]],
        leads: list[dict[str, Any]],
        outreach: list[dict[str, Any]],
        revenue: list[dict[str, Any]],
    ) -> dict[str, Any]:
        business = opportunity.get("business_model") or {}
        totals: defaultdict[str, int] = defaultdict(int)
        for item in revenue:
            currency = str(item.get("currency") or "?").upper()
            totals[currency] += int(item.get("amount_cents") or 0)
        latest_build = build_runs[0] if build_runs else None
        return {
            "id": opportunity.get("id"),
            "title": opportunity.get("title", "Bez nazwy"),
            "customer": opportunity.get("customer", "Nieustalony"),
            "problem": opportunity.get("problem", ""),
            "value": opportunity.get("proposed_value", ""),
            "stage": opportunity.get("stage", "unknown"),
            "discovered_at": opportunity.get("discovered_at"),
            "score": scorecard.get("total") if scorecard else None,
            "recommendation": scorecard.get("recommendation") if scorecard else "not_scored",
            "rationale": scorecard.get("rationale", []) if scorecard else [],
            "evidence_count": len(opportunity.get("evidence") or []),
            "business_model": {
                "name": business.get("name"),
                "payer": business.get("payer"),
                "offer": business.get("offer"),
                "revenue_model": business.get("revenue_model"),
                "delivery_model": business.get("delivery_model"),
                "pricing": business.get("pricing"),
                "acquisition_wedge": business.get("acquisition_wedge"),
                "primary_risks": business.get("primary_risks") or [],
            },
            "validation": {
                "experiments": len(experiments),
                "runs": len(experiment_runs),
                "results": len(validation_results),
                "latest_status": experiment_runs[0].get("status") if experiment_runs else None,
            },
            "build": {
                "runs": len(build_runs),
                "latest_status": latest_build.get("status") if latest_build else None,
                "workspace": latest_build.get("workspace") if latest_build else None,
                "preview_ready": bool(previews),
            },
            "gtm": {
                "leads": len(leads),
                "outreach_runs": len(outreach),
                "latest_status": outreach[0].get("status") if outreach else None,
                "revenue_cents": dict(totals),
            },
        }

    def _approvals(self, db: sqlite3.Connection, tables: set[str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for table, label in APPROVAL_TABLES.items():
            for payload in self._payloads(db, tables, table, "updated_at DESC"):
                if payload.get("status") != "waiting_approval":
                    continue
                items.append(
                    {
                        "kind": label,
                        "run_id": payload.get("id"),
                        "opportunity_id": payload.get("opportunity_id"),
                        "updated_at": payload.get("updated_at"),
                    }
                )
        return items

    def _activity(self, db: sqlite3.Connection, tables: set[str]) -> list[dict[str, Any]]:
        if "events" not in tables:
            return []
        rows = db.execute(
            "SELECT event_type, entity_id, created_at FROM events ORDER BY seq DESC LIMIT 30"
        ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "entity_id": row["entity_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _workspace_files(self) -> list[dict[str, Any]]:
        if not self.workspace_root.is_dir():
            return []
        files: list[dict[str, Any]] = []
        for path in sorted(self.workspace_root.rglob("*"), key=lambda item: str(item)):
            if len(files) >= 100:
                break
            if path.is_symlink() or not path.is_file():
                continue
            files.append(
                {
                    "path": str(path.relative_to(self.workspace_root)),
                    "size_bytes": path.stat().st_size,
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                }
            )
        return files

    @staticmethod
    def _group(items: list[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
        grouped: defaultdict[Any, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            grouped[item.get(key)].append(item)
        return dict(grouped)

    @staticmethod
    def _payloads(
        db: sqlite3.Connection,
        tables: set[str],
        table: str,
        order: str,
    ) -> list[dict[str, Any]]:
        if table not in tables:
            return []
        rows = db.execute(f"SELECT payload FROM {table} ORDER BY {order}").fetchall()
        return [_json(row["payload"]) for row in rows]

    @staticmethod
    def _count(db: sqlite3.Connection, tables: set[str], table: str) -> int:
        if table not in tables:
            return 0
        row = db.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"] if row else 0)

    @staticmethod
    def _latest(
        db: sqlite3.Connection,
        tables: set[str],
        table: str,
        timestamp: str,
    ) -> dict[str, Any] | None:
        if table not in tables:
            return None
        row = db.execute(
            f"SELECT payload FROM {table} ORDER BY {timestamp} DESC LIMIT 1"
        ).fetchone()
        return _json(row["payload"]) if row else None

    @staticmethod
    def _headline(
        ventures: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
        discovery: dict[str, Any] | None,
        scheduler: dict[str, Any] | None,
    ) -> str:
        failed = next(
            (
                (label, item)
                for label, item in (("odkrywanie", discovery), ("realizacja", scheduler))
                if item and item.get("disposition") == "failed"
            ),
            None,
        )
        if failed:
            label, item = failed
            return f"Ostatni przebieg ({label}) nie powiódł się: {item.get('reason', 'brak opisu')}"
        if approvals:
            return f"HELIS czeka na {len(approvals)} decyzji właściciela."
        if not ventures:
            return "HELIS obserwuje rynek; nie wybrał jeszcze przedsięwzięcia."
        building = sum(item["stage"] in {"building", "ready_preview"} for item in ventures)
        if building:
            return f"HELIS rozwija obecnie {building} przedsięwzięcia."
        return f"HELIS śledzi {len(ventures)} pomysłów i czeka na kolejny sygnał."

    def _empty(self, message: str) -> dict[str, Any]:
        return {
            "generated_at": _now(),
            "database": str(self.db),
            "status": "unavailable",
            "message": message,
            "summary": {
                "observations": 0,
                "opportunities": 0,
                "active_ventures": 0,
                "builds": 0,
                "leads": 0,
                "pending_approvals": 0,
            },
            "stages": {},
            "discovery": None,
            "scheduler": None,
            "ventures": [],
            "approvals": [],
            "activity": [],
            "workspace": [],
        }
