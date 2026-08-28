from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from helis.engine import HelisEngine
from helis.portfolio_scheduler import SchedulerTickReport
from helis.scheduler_wake import (
    SchedulerWakeController,
    SchedulerWakeStore,
    WakeDisposition,
    WakeLeaseLost,
    WakePolicy,
)
from helis.store import HelisStore


@dataclass(slots=True)
class FakeTicker:
    calls: int = 0
    fail: bool = False
    last_max_advances: int | None = None

    def tick(self, *, max_advances: int) -> SchedulerTickReport:
        self.calls += 1
        self.last_max_advances = max_advances
        if self.fail:
            raise RuntimeError("simulated scheduler failure")
        return SchedulerTickReport(max_advances=max_advances)


def _engine(tmp_path) -> HelisEngine:
    return HelisEngine(HelisStore(tmp_path / "helis.db"))


def test_first_wake_runs_and_second_inside_interval_is_not_due(tmp_path) -> None:
    engine = _engine(tmp_path)
    ticker = FakeTicker()
    controller = SchedulerWakeController(engine, ticker)
    policy = WakePolicy(minimum_interval_seconds=900, lease_seconds=120, max_advances=3)
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    first = controller.wake(policy, now=start)
    second = controller.wake(policy, now=start + timedelta(seconds=60))

    assert first.disposition == WakeDisposition.RAN
    assert first.scheduler_report_id is not None
    assert ticker.calls == 1
    assert ticker.last_max_advances == 3
    assert second.disposition == WakeDisposition.NOT_DUE
    assert second.owner_id is None
    assert SchedulerWakeStore(engine).latest_result().id == second.id


def test_active_unexpired_lease_blocks_another_worker(tmp_path) -> None:
    engine = _engine(tmp_path)
    state = SchedulerWakeStore(engine)
    policy = WakePolicy(minimum_interval_seconds=0, lease_seconds=300)
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    first_owner = uuid4()
    disposition, _ = state.acquire(policy, owner_id=first_owner, now=start)
    assert disposition is None

    ticker = FakeTicker()
    result = SchedulerWakeController(engine, ticker).wake(
        policy,
        now=start + timedelta(seconds=30),
    )

    assert result.disposition == WakeDisposition.LEASE_HELD
    assert ticker.calls == 0


def test_expired_lease_can_be_reclaimed(tmp_path) -> None:
    engine = _engine(tmp_path)
    state = SchedulerWakeStore(engine)
    policy = WakePolicy(minimum_interval_seconds=0, lease_seconds=60)
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    disposition, _ = state.acquire(policy, owner_id=uuid4(), now=start)
    assert disposition is None

    ticker = FakeTicker()
    result = SchedulerWakeController(engine, ticker).wake(
        policy,
        now=start + timedelta(seconds=61),
    )

    assert result.disposition == WakeDisposition.RAN
    assert ticker.calls == 1


def test_failed_tick_releases_lease_but_immediate_retry_is_throttled(tmp_path) -> None:
    engine = _engine(tmp_path)
    failing = FakeTicker(fail=True)
    policy = WakePolicy(minimum_interval_seconds=900, lease_seconds=120)
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    failed = SchedulerWakeController(engine, failing).wake(policy, now=start)
    assert failed.disposition == WakeDisposition.FAILED
    assert failing.calls == 1

    healthy = FakeTicker()
    retry = SchedulerWakeController(engine, healthy).wake(
        policy,
        now=start + timedelta(seconds=10),
    )
    assert retry.disposition == WakeDisposition.NOT_DUE
    assert healthy.calls == 0

    later = SchedulerWakeController(engine, healthy).wake(
        policy,
        now=start + timedelta(seconds=901),
    )
    assert later.disposition == WakeDisposition.RAN
    assert healthy.calls == 1


def test_only_current_lease_owner_can_finish(tmp_path) -> None:
    engine = _engine(tmp_path)
    state = SchedulerWakeStore(engine)
    policy = WakePolicy(minimum_interval_seconds=0, lease_seconds=120)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    owner = uuid4()
    disposition, _ = state.acquire(policy, owner_id=owner, now=now)
    assert disposition is None

    with pytest.raises(WakeLeaseLost):
        state.finish_owned(
            owner_id=uuid4(),
            now=now + timedelta(seconds=1),
            scheduler_report_id=None,
            mark_completed=False,
        )

    # The actual owner can still finish after the rejected foreign release attempt.
    state.finish_owned(
        owner_id=owner,
        now=now + timedelta(seconds=2),
        scheduler_report_id=None,
        mark_completed=False,
    )


def test_reclaimed_lease_cannot_be_cleared_by_old_owner(tmp_path) -> None:
    engine = _engine(tmp_path)
    state = SchedulerWakeStore(engine)
    policy = WakePolicy(minimum_interval_seconds=0, lease_seconds=30)
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    old_owner = uuid4()
    new_owner = uuid4()
    disposition, _ = state.acquire(policy, owner_id=old_owner, now=start)
    assert disposition is None
    disposition, _ = state.acquire(
        policy,
        owner_id=new_owner,
        now=start + timedelta(seconds=31),
    )
    assert disposition is None

    with pytest.raises(WakeLeaseLost):
        state.finish_owned(
            owner_id=old_owner,
            now=start + timedelta(seconds=32),
            scheduler_report_id=None,
            mark_completed=False,
        )

    state.finish_owned(
        owner_id=new_owner,
        now=start + timedelta(seconds=33),
        scheduler_report_id=None,
        mark_completed=False,
    )
