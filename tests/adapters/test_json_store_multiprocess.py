from __future__ import annotations

import asyncio
import json
import multiprocessing

import pytest

from novacode.adapters.json_store import AtomicJsonStore, ProcessFileLock
from novacode.runtime.errors import OperationTimeout


def _hold_lock(path: str, ready: multiprocessing.synchronize.Event, release) -> None:
    async def run() -> None:
        async with ProcessFileLock(path, acquire_timeout=2):
            ready.set()
            await asyncio.to_thread(release.wait, 5)

    asyncio.run(run())


def _append_item(path: str, item: str, start, errors) -> None:
    async def run() -> None:
        store = AtomicJsonStore(path)
        await asyncio.to_thread(start.wait, 5)

        def append(current):
            values = dict(current)
            values["items"] = [*values.get("items", []), item]
            return values

        await store.transact({"items": []}, append)

    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - 子进程必须把失败传回父进程
        errors.put(repr(exc))


@pytest.mark.asyncio
async def test_live_lock_cannot_be_reclaimed(isolated_state, spawn_context) -> None:
    lock_path = isolated_state.root / "aggregate.lock"
    ready = spawn_context.Event()
    release = spawn_context.Event()
    process = spawn_context.Process(target=_hold_lock, args=(str(lock_path), ready, release))
    process.start()
    assert await asyncio.to_thread(ready.wait, 5)

    with pytest.raises(OperationTimeout):
        await ProcessFileLock(lock_path, acquire_timeout=0.15).acquire()

    release.set()
    await asyncio.to_thread(process.join, 5)
    assert process.exitcode == 0
    assert not lock_path.exists()


@pytest.mark.asyncio
async def test_lock_release_requires_matching_ownership_token(isolated_state) -> None:
    lock_path = isolated_state.root / "aggregate.lock"
    lock = ProcessFileLock(lock_path)
    await lock.acquire()
    owner = json.loads(lock_path.read_text(encoding="utf-8"))
    owner["token"] = "different-owner"
    lock_path.write_text(json.dumps(owner), encoding="utf-8")

    await lock.release()

    assert lock_path.exists()
    lock_path.unlink()


@pytest.mark.asyncio
async def test_two_process_transactions_do_not_lose_updates(isolated_state, spawn_context) -> None:
    state_path = isolated_state.root / "aggregate.json"
    start = spawn_context.Event()
    errors = spawn_context.Queue()
    processes = [
        spawn_context.Process(target=_append_item, args=(str(state_path), item, start, errors))
        for item in ("first", "second")
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        await asyncio.to_thread(process.join, 10)

    assert [process.exitcode for process in processes] == [0, 0]
    assert errors.empty()
    stored = await AtomicJsonStore(state_path).read({"items": []})
    assert set(stored["items"]) == {"first", "second"}
