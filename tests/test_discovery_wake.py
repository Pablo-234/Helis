from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from helis.discovery_wake import (
    DiscoveryRuntime,
    DiscoveryWakeController,
    DiscoveryWakeDisposition,
    DiscoveryWakePolicy,
    DiscoveryWakeResult,
    DiscoveryWakeStore,
)
from helis.domain import Observation
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.source_registry import RegistryScanResult, ScanFailure
from helis.store import HelisStore


@dataclass(slots=True)
class FakeProvider:
    calls: int = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        raise AssertionError("model call was not expected")


@dataclass(slots=True)
class FakeScanner:
    result: RegistryScanResult
    calls: int = 0

    def scan(self) -> RegistryScanResult:
        self.calls += 1
        return self.result


@dataclass(slots=True)
class FakeRuntime:
    result: DiscoveryWakeResult | None = None
    error: Exception | None = None
    calls: int = 0

    def tick(self, policy: DiscoveryWakePolicy) -> DiscoveryWakeResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
        return DiscoveryWakeResult(
            disposition=DiscoveryWakeDisposition.RAN,
            reason="fake_runtime_completed",
            attempted_at=now,
            completed_at=now,
        )


def _engine(tmp_path) -> HelisEngine:
    return HelisEngine(HelisStore(tmp_path / "helis.db"))


def test_empty_scan_and_empty_backlog_use_zero_model_calls(tmp_path) -> None:
    engine = _engine(tmp_path)
    provider = FakeProvider()
    scanner = FakeScanner(RegistryScanResult())
    runtime = DiscoveryRuntime(engine, provider, lambda: scanner)

    result = runtime.tick(DiscoveryWakePolicy(max_model_calls=0))

    assert result.disposition == DiscoveryWakeDisposition.RAN
    assert result.did_work is False
    assert result.model_calls == 0
    assert provider.calls == 0
    assert scanner.calls == 1


def test_source_failure_is_isolated_and_does_not_force_model_call(tmp_path) -> None:
    engine = _engine(tmp_path)
    provider = FakeProvider()
    scanner = FakeScanner(
        RegistryScanResult(failures=[ScanFailure(source_name="broken", error="boom")])
    )
    runtime = DiscoveryRuntime(engine, provider, lambda: scanner)

    result = runtime.tick(DiscoveryWakePolicy(max_model_calls=0))

    assert result.disposition == DiscoveryWakeDisposition.RAN
    assert result.source_failures == 1
    assert result.model_calls == 0
    assert provider.calls == 0


def test_zero_model_capacity_persists_new_observation_for_resume(tmp_path) -> None:
    engine = _engine(tmp_path)
    provider = FakeProvider()
    observation = Observation(
        id=uuid4(),
        text="Operators repeatedly complain about a slow manual reconciliation workflow.",
        source="test-source",
    )
    scanner = FakeScanner(RegistryScanResult(observations=[observation]))
    runtime = DiscoveryRuntime(engine, provider, lambda: scanner)

    result = runtime.tick(DiscoveryWakePolicy(max_model_calls=0))

    assert result.observations_fetched == 1
    assert result.observations_new == 1
    assert result.observations_used == 1
    assert result.budget_exhausted is True
    assert result.model_calls == 0
    assert provider.calls == 0
    pending = engine.store.list_unprocessed_observations(limit=10)
    assert [item.id for item in pending] == [observation.id]


def test_second_wake_before_due_does_not_run_runtime(tmp_path) -> None:
    engine = _engine(tmp_path)
    runtime = FakeRuntime()
    controller = DiscoveryWakeController(engine, runtime)  # type: ignore[arg-type]
    policy = DiscoveryWakePolicy(minimum_interval_seconds=3600)
    first_at = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    first = controller.wake(policy, now=first_at)
    second = controller.wake(policy, now=first_at + timedelta(minutes=5))

    assert first.disposition == DiscoveryWakeDisposition.RAN
    assert second.disposition == DiscoveryWakeDisposition.NOT_DUE
    assert runtime.calls == 1


def test_active_discovery_lease_blocks_second_worker(tmp_path) -> None:
    engine = _engine(tmp_path)
    runtime = FakeRuntime()
    controller = DiscoveryWakeController(engine, runtime)  # type: ignore[arg-type]
    policy = DiscoveryWakePolicy(minimum_interval_seconds=0, lease_seconds=900)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    store = DiscoveryWakeStore(engine)
    disposition, _ = store.acquire(policy, owner_id=uuid4(), now=now)

    result = controller.wake(policy, now=now)

    assert disposition is None
    assert result.disposition == DiscoveryWakeDisposition.LEASE_HELD
    assert runtime.calls == 0


def test_runtime_failure_releases_lease_for_next_wake(tmp_path) -> None:
    engine = _engine(tmp_path)
    failing = FakeRuntime(error=RuntimeError("source config exploded"))
    first_controller = DiscoveryWakeController(engine, failing)  # type: ignore[arg-type]
    policy = DiscoveryWakePolicy(minimum_interval_seconds=0, lease_seconds=900)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    failed = first_controller.wake(policy, now=now)

    succeeding = FakeRuntime()
    second_controller = DiscoveryWakeController(engine, succeeding)  # type: ignore[arg-type]
    recovered = second_controller.wake(policy, now=now + timedelta(seconds=1))

    assert failed.disposition == DiscoveryWakeDisposition.FAILED
    assert "source config exploded" in failed.reason
    assert recovered.disposition == DiscoveryWakeDisposition.RAN
    assert succeeding.calls == 1


def test_runtime_failure_is_bounded_and_persisted_even_when_detail_is_huge(tmp_path) -> None:
    engine = _engine(tmp_path)
    runtime = FakeRuntime(error=RuntimeError("sensitive detail " * 500))
    controller = DiscoveryWakeController(engine, runtime)  # type: ignore[arg-type]

    failed = controller.wake(DiscoveryWakePolicy(minimum_interval_seconds=0))
    persisted = DiscoveryWakeStore(engine).latest_result()

    assert failed.disposition == DiscoveryWakeDisposition.FAILED
    assert len(failed.reason) == 1000
    assert failed.reason.endswith("...")
    assert persisted is not None
    assert persisted.reason == failed.reason


def test_discovery_wakes_are_audited(tmp_path) -> None:
    engine = _engine(tmp_path)
    runtime = FakeRuntime()
    controller = DiscoveryWakeController(engine, runtime)  # type: ignore[arg-type]

    controller.wake(DiscoveryWakePolicy(minimum_interval_seconds=0))

    with engine.store.connect() as db:
        row = db.execute(
            "SELECT COUNT(*) AS count FROM events WHERE event_type = 'discovery.wake'"
        ).fetchone()
    assert row is not None
    assert row["count"] == 1
