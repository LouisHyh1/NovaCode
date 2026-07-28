"""Tests for gated, background memory governance."""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from novacode.memory import MemoryAction, MemoryGovernor, MemoryKind, MemoryStore
from novacode.memory.governor import _ConsolidationLock

NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def user_store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory", frozenset({MemoryKind.USER, MemoryKind.FEEDBACK}))


def eligible_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novacode.memory.governor.list_sessions", lambda _: [object()] * 5)


def age(path, delta: timedelta) -> None:
    timestamp = (NOW - delta).timestamp()
    os.utime(path, (timestamp, timestamp))


def wall_age(path, delta: timedelta) -> None:
    timestamp = time.time() - delta.total_seconds()
    os.utime(path, (timestamp, timestamp))


async def no_changes(**_):
    return []


def test_scan_throttle_uses_old_time_and_failed_gate_keeps_new_time(tmp_path) -> None:
    governor = MemoryGovernor(
        tmp_path / "sessions", (user_store(tmp_path),), no_changes, lambda _: None
    )

    assert governor.maybe_schedule(NOW) is False
    assert governor.last_scan_at == NOW
    assert governor.maybe_schedule(NOW + timedelta(minutes=5)) is False
    assert governor.last_scan_at == NOW


def test_recent_success_and_too_few_sessions_do_not_schedule(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = user_store(tmp_path)
    store.directory.mkdir()
    governor = MemoryGovernor(tmp_path / "sessions", (store,), no_changes, lambda _: None)
    governor.lock_path.write_text("\n", encoding="ascii")
    os.utime(governor.lock_path, (NOW.timestamp(), NOW.timestamp()))

    eligible_sessions(monkeypatch)
    assert governor.maybe_schedule(NOW) is False

    age(governor.lock_path, timedelta(days=2))
    monkeypatch.setattr("novacode.memory.governor.list_sessions", lambda _: [object()] * 4)
    assert governor.maybe_schedule(NOW + timedelta(minutes=11)) is False


@pytest.mark.asyncio
async def test_all_gates_schedule_one_nonblocking_task_and_success_updates_mtime(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = user_store(tmp_path)
    store.directory.mkdir()
    entered = asyncio.Event()
    release = asyncio.Event()
    notices: list[str] = []

    async def runner(**_):
        entered.set()
        await release.wait()
        return [
            MemoryAction("create", MemoryKind.USER, title="Stable", summary="short", content="body")
        ]

    eligible_sessions(monkeypatch)
    governor = MemoryGovernor(tmp_path / "sessions", (store,), runner, notices.append)
    governor.lock_path.parent.mkdir(parents=True, exist_ok=True)
    governor.lock_path.write_text("\n", encoding="ascii")
    age(governor.lock_path, timedelta(days=2))
    original = governor.lock_path.stat().st_mtime

    assert governor.maybe_schedule(NOW) is True
    assert governor.maybe_schedule(NOW + timedelta(minutes=11)) is False
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert notices == []
    release.set()
    await governor.wait()

    assert governor.lock_path.stat().st_mtime > original
    assert notices == ["memory governance completed: create=1 update=0 delete=0"]
    assert "Stable" in (store.directory / "MEMORY.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_failure_and_cancel_restore_exact_previous_mtime(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = user_store(tmp_path)
    store.directory.mkdir()
    eligible_sessions(monkeypatch)
    notices: list[str] = []

    async def fail(**_):
        raise RuntimeError("secret body must not leak")

    governor = MemoryGovernor(tmp_path / "sessions", (store,), fail, notices.append)
    governor.lock_path.parent.mkdir(parents=True, exist_ok=True)
    governor.lock_path.write_text("\n", encoding="ascii")
    age(governor.lock_path, timedelta(days=2))
    previous = governor.lock_path.stat().st_mtime_ns
    assert governor.maybe_schedule(NOW) is True
    await governor.wait()

    assert governor.lock_path.stat().st_mtime_ns == previous
    assert notices == ["memory governance failed: create=0 update=0 delete=0"]
    assert "secret" not in notices[0]

    entered = asyncio.Event()

    async def hang(**_):
        entered.set()
        await asyncio.Event().wait()

    governor = MemoryGovernor(tmp_path / "sessions", (store,), hang, notices.append)
    age(governor.lock_path, timedelta(days=2))
    previous = governor.lock_path.stat().st_mtime_ns
    assert governor.maybe_schedule(NOW + timedelta(minutes=20)) is True
    await entered.wait()
    await governor.close()
    assert governor.lock_path.stat().st_mtime_ns == previous


def test_consolidation_lock_pid_and_unknown_age_rules(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".consolidate-lock"
    path.write_text(str(os.getpid()), encoding="ascii")
    wall_age(path, timedelta(days=2))
    assert _ConsolidationLock(path).acquire() is False

    path.write_text("99999999", encoding="ascii")
    wall_age(path, timedelta(minutes=5))
    dead = _ConsolidationLock(path)
    assert dead.acquire() is True
    dead.release(success=False)

    monkeypatch.setattr("novacode.memory.governor._pid_status", lambda _: None)
    path.write_text("123", encoding="ascii")
    wall_age(path, timedelta(minutes=5))
    assert _ConsolidationLock(path).acquire() is False
    wall_age(path, timedelta(hours=2))
    stale = _ConsolidationLock(path)
    assert stale.acquire() is True
    stale.release(success=False)


@pytest.mark.asyncio
async def test_restricted_request_and_store_validation_limit_writes_to_target(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = user_store(tmp_path)
    store.directory.mkdir()
    requests = []

    async def runner(**kwargs):
        requests.append(kwargs)
        return [
            MemoryAction("create", MemoryKind.USER, title="Allowed", summary="ok", content="ok"),
            MemoryAction("create", MemoryKind.PROJECT, title="Wrong", summary="bad", content="bad"),
            MemoryAction(
                "create",
                MemoryKind.USER,
                title="Escape",
                summary="bad",
                content="bad",
                filename="../outside.md",
            ),
        ]

    eligible_sessions(monkeypatch)
    notices: list[str] = []
    governor = MemoryGovernor(tmp_path / "sessions", (store,), runner, notices.append)
    assert governor.maybe_schedule(NOW) is True
    await governor.wait()

    assert set(requests[0]) == {
        "sessions",
        "indexes",
        "target_directory",
        "allowed_kinds",
        "prompt",
    }
    assert requests[0]["target_directory"] == store.directory.resolve()
    assert not (tmp_path / "outside.md").exists()
    assert "Allowed" in (store.directory / "MEMORY.md").read_text(encoding="utf-8")
    assert "Wrong" not in (store.directory / "MEMORY.md").read_text(encoding="utf-8")
    assert notices == ["memory governance completed: create=1 update=0 delete=0"]
