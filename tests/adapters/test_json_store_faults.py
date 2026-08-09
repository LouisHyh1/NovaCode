from __future__ import annotations

import asyncio
import json
import time

import pytest

from novacode.adapters.json_store import AtomicJsonStore, ProcessFileLock
from novacode.runtime.errors import CleanupError


@pytest.mark.asyncio
async def test_dead_process_lock_is_reclaimed(isolated_state) -> None:
    lock_path = isolated_state.root / "aggregate.lock"
    lock_path.write_text(
        json.dumps({"pid": 2**30, "created_at": 1.0, "token": "dead-owner"}),
        encoding="utf-8",
    )

    async with ProcessFileLock(lock_path, acquire_timeout=1):
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
        assert owner["token"] != "dead-owner"

    assert not lock_path.exists()


@pytest.mark.asyncio
async def test_transaction_file_io_does_not_block_event_loop(isolated_state) -> None:
    state_path = isolated_state.root / "aggregate.json"
    entered_io = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow_disk_phase(phase, _path) -> None:
        if phase == "before_candidate_write":
            loop.call_soon_threadsafe(entered_io.set)
            time.sleep(0.15)

    store = AtomicJsonStore(state_path, fault_hook=slow_disk_phase)
    transaction = asyncio.create_task(store.transact({"value": 0}, lambda _: {"value": 1}))
    await entered_io.wait()
    await asyncio.sleep(0.02)

    assert not transaction.done()
    assert (await transaction)["value"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    ("after_lock", "before_candidate_write", "after_candidate_write", "before_publish"),
)
async def test_failure_before_publication_preserves_authoritative_state(
    isolated_state, phase
) -> None:
    state_path = isolated_state.root / "aggregate.json"
    initial = AtomicJsonStore(state_path)
    await initial.transact({"value": 0}, lambda _: {"value": 1})

    def fail(selected, _path) -> None:
        if selected == phase:
            raise RuntimeError(f"injected:{phase}")

    failing = AtomicJsonStore(state_path, fault_hook=fail)
    with pytest.raises(RuntimeError, match=f"injected:{phase}"):
        await failing.transact({"value": 0}, lambda _: {"value": 2})

    assert (await initial.read({}))["value"] == 1
    assert not list(state_path.parent.glob(f".{state_path.name}.*.tmp"))


@pytest.mark.asyncio
async def test_failure_after_publication_exposes_only_complete_candidate(isolated_state) -> None:
    state_path = isolated_state.root / "aggregate.json"
    initial = AtomicJsonStore(state_path)
    await initial.transact({"items": []}, lambda _: {"items": ["old"]})

    def fail_after_publish(phase, _path) -> None:
        if phase == "after_publish":
            raise RuntimeError("injected:after_publish")

    failing = AtomicJsonStore(state_path, fault_hook=fail_after_publish)
    with pytest.raises(RuntimeError, match="injected:after_publish"):
        await failing.transact({"items": []}, lambda _: {"items": ["new", "complete"]})

    assert (await initial.read({}))["items"] == ["new", "complete"]


@pytest.mark.asyncio
async def test_cleanup_failure_is_typed_and_preserves_residual_evidence(isolated_state) -> None:
    state_path = isolated_state.root / "aggregate.json"
    initial = AtomicJsonStore(state_path)
    await initial.transact({"value": 0}, lambda _: {"value": 1})

    def fail_publish_and_cleanup(phase, _path) -> None:
        if phase == "before_publish":
            raise RuntimeError("injected:before_publish")
        if phase == "cleanup":
            raise OSError("injected:cleanup")

    failing = AtomicJsonStore(state_path, fault_hook=fail_publish_and_cleanup)
    with pytest.raises(CleanupError) as captured:
        await failing.transact({"value": 0}, lambda _: {"value": 2})

    assert (await initial.read({}))["value"] == 1
    assert captured.value.residual_path
    assert len(list(state_path.parent.glob(f".{state_path.name}.*.tmp"))) == 1
