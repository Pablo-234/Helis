from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from helis.dashboard import DashboardSnapshotBuilder
from helis.dashboard_web import dashboard_server
from helis.discovery_wake import (
    DiscoveryWakeDisposition,
    DiscoveryWakeResult,
    DiscoveryWakeStore,
)
from helis.domain import Evidence, EvidenceKind, Opportunity, Scorecard, ScoreDimensions
from helis.engine import HelisEngine
from helis.store import HelisStore


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "helis.db"
    HelisStore(path).initialize()
    return path


def test_empty_snapshot_explains_that_discovery_is_still_running(tmp_path: Path) -> None:
    db = _database(tmp_path)

    snapshot = DashboardSnapshotBuilder(db, tmp_path / "workspaces").build()

    assert snapshot["status"] == "ok"
    assert snapshot["summary"]["opportunities"] == 0
    assert snapshot["summary"]["pending_approvals"] == 0
    assert snapshot["ventures"] == []
    assert "nie wybrał jeszcze" in snapshot["message"]


def test_snapshot_prioritizes_latest_failed_loop_and_exposes_safe_reason(tmp_path: Path) -> None:
    db = _database(tmp_path)
    store = HelisStore(db)
    failed = DiscoveryWakeResult(
        disposition=DiscoveryWakeDisposition.FAILED,
        reason="ModelResponseError: model returned empty final content",
        attempted_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
    )
    DiscoveryWakeStore(HelisEngine(store)).save_result(failed)

    snapshot = DashboardSnapshotBuilder(db, tmp_path / "workspaces").build()

    assert snapshot["discovery"]["disposition"] == "failed"
    assert "nie powiódł się" in snapshot["message"]
    assert failed.reason in snapshot["message"]


def test_snapshot_joins_owner_relevant_venture_state(tmp_path: Path) -> None:
    db = _database(tmp_path)
    store = HelisStore(db)
    opportunity = Opportunity(
        title="Automatyczne odpowiedzi na zapytania ofertowe",
        problem="Małe firmy tracą klientów, gdy odpowiadają na zapytania zbyt późno.",
        customer="lokalne firmy usługowe",
        proposed_value="Odpowiedź i kwalifikacja zapytania w kilka minut.",
        evidence=[
            Evidence(
                kind=EvidenceKind.CUSTOMER_PAIN,
                claim="Publiczne zgłoszenie opisuje utracone zapytania.",
                source="https://example.test/signal",
            )
        ],
    )
    store.save_opportunity(opportunity)
    store.save_scorecard(
        Scorecard(
            opportunity_id=opportunity.id,
            dimensions=ScoreDimensions(),
            total=73,
            recommendation="validate",
            rationale=["częsty problem", "tani test"],
        )
    )

    snapshot = DashboardSnapshotBuilder(db, tmp_path / "workspaces").build()

    assert snapshot["summary"]["opportunities"] == 1
    venture = snapshot["ventures"][0]
    assert venture["title"] == opportunity.title
    assert venture["score"] == 73
    assert venture["recommendation"] == "validate"
    assert venture["evidence_count"] == 1
    assert venture["build"]["runs"] == 0
    assert venture["gtm"]["leads"] == 0


def test_missing_database_returns_safe_unavailable_snapshot(tmp_path: Path) -> None:
    snapshot = DashboardSnapshotBuilder(tmp_path / "missing.db").build()

    assert snapshot["status"] == "unavailable"
    assert snapshot["summary"]["opportunities"] == 0
    assert not (tmp_path / "missing.db").exists()


def test_workspace_inventory_skips_symlinks_and_never_reads_file_content(tmp_path: Path) -> None:
    db = _database(tmp_path)
    root = tmp_path / "workspaces"
    root.mkdir()
    artifact = root / "venture" / "index.html"
    artifact.parent.mkdir()
    artifact.write_text("super-secret-artifact-body", encoding="utf-8")
    link = root / "outside"
    try:
        link.symlink_to(tmp_path / "outside.txt")
    except OSError:
        pass

    snapshot = DashboardSnapshotBuilder(db, root).build()

    assert snapshot["workspace"] == [
        {
            "path": "venture/index.html",
            "size_bytes": len("super-secret-artifact-body"),
            "modified_at": snapshot["workspace"][0]["modified_at"],
        }
    ]
    assert "super-secret-artifact-body" not in json.dumps(snapshot)


def test_http_dashboard_is_local_read_only_and_serves_snapshot(tmp_path: Path) -> None:
    db = _database(tmp_path)
    server = dashboard_server(db, tmp_path / "workspaces", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base, timeout=2) as response:
            html = response.read().decode("utf-8")
            assert response.headers["Content-Security-Policy"]
        assert "HELIS" in html
        assert "tylko do odczytu" in html

        with urlopen(f"{base}/api/snapshot", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["summary"]["opportunities"] == 0

        with pytest.raises(HTTPError) as error:
            urlopen(Request(f"{base}/api/snapshot", method="POST"), timeout=2)
        assert error.value.code == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_rejects_non_loopback_binding(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        dashboard_server(tmp_path / "helis.db", tmp_path / "workspaces", host="0.0.0.0")
