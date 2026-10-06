"""Tests for gated, background memory governance."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from novacode.memory import MemoryAction, MemoryGovernor, MemoryKind, MemoryStore
from novacode.memory.governor import _ConsolidationLock, _pid_status
from novacode.memory.prompts import MAX_GOVERNANCE_CHARS
from novacode.session import list_sessions

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


def test_pid_status_recognizes_current_process_without_signalling_it() -> None:
    assert _pid_status(os.getpid()) is True


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
    provider = object()
    governor.bind_provider(provider)
    assert governor.maybe_schedule(NOW) is True
    await governor.wait()

    assert set(requests[0]) == {
        "provider",
        "sessions",
        "indexes",
        "target_directory",
        "allowed_kinds",
        "prompt",
    }
    assert requests[0]["provider"] is provider
    assert requests[0]["target_directory"] == store.directory.resolve()
    assert not (tmp_path / "outside.md").exists()
    assert "Allowed" in (store.directory / "MEMORY.md").read_text(encoding="utf-8")
    assert "Wrong" not in (store.directory / "MEMORY.md").read_text(encoding="utf-8")
    assert notices == ["memory governance completed: create=1 update=0 delete=0"]


def write_sessions(directory, count, content_size=10):
    directory.mkdir(exist_ok=True)
    for index in range(count):
        path = directory / f"20260720-120000-{index:04x}.jsonl"
        records = [
            {
                "type": "message",
                "role": "user",
                "model": "test",
                "ts": (NOW + timedelta(minutes=index)).isoformat(),
                "content": f"history-{index:02d}:" + "x" * content_size,
            },
            {
                "type": "message",
                "role": "assistant",
                "ts": (NOW + timedelta(minutes=index, seconds=1)).isoformat(),
                "content": f"latest-{index:02d}",
            },
        ]
        path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
        )


@pytest.mark.asyncio
async def test_governor_selects_latest_twenty_sessions(tmp_path) -> None:
    store = user_store(tmp_path)
    store.directory.mkdir()
    directory = tmp_path / "sessions"
    write_sessions(directory, 25)
    received = []

    async def runner(**kwargs):
        received.extend(kwargs["sessions"])
        return []

    governor = MemoryGovernor(directory, (store,), runner, lambda _: None)
    assert governor.maybe_schedule(NOW)
    await governor.wait()
    assert [info.session_id for info in received] == [
        f"20260720-120000-{index:04x}" for index in range(24, 4, -1)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("content_size", [10, 60_000])
async def test_real_governance_runner_budgets_entire_request_newest_first(
    tmp_path, content_size
) -> None:
    from novacode.cli import _restricted_governance_runner
    from novacode.llm import StreamEvent

    directory = tmp_path / "sessions"
    write_sessions(directory, 25, content_size)
    target = tmp_path / "notes"
    target.mkdir()
    (target / "MEMORY.md").write_text("m" * 60_000, encoding="utf-8")
    requests = []

    class Provider:
        close_calls = 0

        async def stream(self, request):
            requests.append(request)
            yield StreamEvent(text="[]", done=True)

        async def close(self):
            self.close_calls += 1

    provider = Provider()
    kwargs = dict(
        provider=provider,
        sessions=tuple(reversed(list_sessions(directory))),
        indexes=("i" * 60_000,),
        target_directory=target,
        allowed_kinds=frozenset({MemoryKind.USER}),
        prompt="Consolidate memory.",
    )
    await _restricted_governance_runner(**kwargs)
    await _restricted_governance_runner(**kwargs)
    content = requests[0].messages[0].content
    assert content == requests[1].messages[0].content
    assert len(content) == MAX_GOVERNANCE_CHARS
    assert "latest-24" in content and "history-24" in content
    assert content.index("latest-24") < content.index("history-24")
    assert "history-04" not in content
    if content_size == 10:
        assert "history-05" in content
    else:
        assert "history-23" not in content
    assert content.endswith("Return only a JSON array of create, update, delete, or no-op actions.")
    assert provider.close_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_governor_completion_and_cancel_do_not_close_borrowed_provider(tmp_path, cancel):
    from novacode.cli import _restricted_governance_runner
    from novacode.llm import StreamEvent

    store = user_store(tmp_path)
    store.directory.mkdir()
    directory = tmp_path / "sessions"
    write_sessions(directory, 5)
    entered = asyncio.Event()
    release = asyncio.Event()

    class Provider:
        close_calls = 0

        async def stream(self, request):
            entered.set()
            await release.wait()
            yield StreamEvent(text="[]", done=True)

        async def close(self):
            self.close_calls += 1

    provider = Provider()
    governor = MemoryGovernor(directory, (store,), _restricted_governance_runner, lambda _: None)
    governor.bind_provider(provider)
    assert governor.maybe_schedule(NOW)
    await asyncio.wait_for(entered.wait(), 1)
    if cancel:
        await governor.close()
    else:
        release.set()
        await governor.wait()
        await governor.close()
    assert provider.close_calls == 0
    assert governor.provider is provider
    assert not store.lock.locked()
