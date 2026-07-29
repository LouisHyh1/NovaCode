import json
import os
from pathlib import Path

import pytest

from novacode.memory import (
    MemoryAction,
    MemoryKind,
    MemoryLockError,
    MemoryRecoveryRequired,
    MemoryStore,
)
from novacode.memory.store import _pid_alive

USER_ID = "11111111-1111-4111-8111-111111111111"


def _create(
    *,
    memory_id: str = USER_ID,
    kind: MemoryKind = MemoryKind.USER,
    title: str = "Preferred language",
    summary: str = "User prefers Chinese replies",
    content: str = "The user prefers technical replies in Chinese.",
    filename: str = "",
) -> MemoryAction:
    return MemoryAction(
        action="create",
        kind=kind,
        memory_id=memory_id,
        title=title,
        summary=summary,
        content=content,
        filename=filename,
    )


@pytest.mark.asyncio
async def test_create_writes_frontmatter_note_and_clickable_index(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", frozenset({MemoryKind.USER, MemoryKind.FEEDBACK}))

    async with store.locked():
        report = store.apply_locked([_create()])
        index = store.read_index_locked()

    assert report.created == 1
    note = store.directory / f"{USER_ID}.md"
    text = note.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert f'id: "{USER_ID}"' in text
    assert 'type: "user"' in text
    assert 'title: "Preferred language"' in text
    assert "created:" in text and "updated:" in text
    assert "The user prefers technical replies in Chinese." in text
    assert "[Preferred language](11111111-1111-4111-8111-111111111111.md)" in index
    assert "User prefers Chinese replies" in index
    assert "technical replies" not in index


@pytest.mark.asyncio
async def test_store_applies_delete_update_create_then_noop(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", frozenset({MemoryKind.USER}))
    second_id = "22222222-2222-4222-8222-222222222222"
    third_id = "33333333-3333-4333-8333-333333333333"
    async with store.locked():
        store.apply_locked(
            [
                _create(),
                _create(memory_id=second_id, title="Second", summary="second", content="second"),
            ]
        )
        report = store.apply_locked(
            [
                MemoryAction(action="no-op"),
                _create(memory_id=third_id, title="Third", summary="third", content="third"),
                MemoryAction(
                    action="update",
                    kind=MemoryKind.USER,
                    memory_id=second_id,
                    title="Updated",
                    summary="updated summary",
                    content="updated content",
                ),
                MemoryAction(action="delete", kind=MemoryKind.USER, memory_id=USER_ID),
            ]
        )
        index = store.read_index_locked()

    assert report.created == 1 and report.updated == 1 and report.deleted == 1
    assert not (store.directory / f"{USER_ID}.md").exists()
    assert "Updated" in index and "Preferred language" not in index


@pytest.mark.asyncio
async def test_store_rejects_wrong_route_and_unsafe_filename(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", frozenset({MemoryKind.USER}))

    async with store.locked():
        report = store.apply_locked(
            [
                _create(kind=MemoryKind.PROJECT),
                _create(filename="../escape.md"),
                _create(filename="MEMORY.md"),
            ]
        )

    assert report.rejected == 3
    assert list(store.directory.glob("*.md")) == []
    assert not (tmp_path / "escape.md").exists()


@pytest.mark.asyncio
async def test_index_line_limit_rejects_only_operation_that_crosses_limit(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", frozenset({MemoryKind.USER}))
    actions = [
        _create(
            memory_id=f"{index:08x}-0000-4000-8000-000000000000",
            title=f"T{index}",
            summary="s",
            content="c",
        )
        for index in range(199)
    ]

    async with store.locked():
        report = store.apply_locked(actions)
        index = store.read_index_locked()

    assert report.created == 198 and report.rejected == 1
    assert len(index.splitlines()) == 200


@pytest.mark.asyncio
async def test_index_byte_limit_is_checked_before_files_are_published(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", frozenset({MemoryKind.USER}))

    async with store.locked():
        report = store.apply_locked([_create(summary="x" * (25 * 1024))])

    assert report.rejected == 1
    assert not (store.directory / f"{USER_ID}.md").exists()
    assert not (store.directory / "MEMORY.md").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_failure", [1, 2, 3])
async def test_transaction_recovers_each_replace_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_failure: int,
) -> None:
    directory = tmp_path / f"memory-{replace_failure}"
    store = MemoryStore(directory, frozenset({MemoryKind.USER}))
    original_replace = os.replace
    calls = 0

    def fail_once(source, target) -> None:
        nonlocal calls
        calls += 1
        if calls == replace_failure:
            raise OSError("crash")
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_once)
    async with store.locked():
        with pytest.raises(OSError, match="crash"):
            store.apply_locked([_create()])

    monkeypatch.setattr(os, "replace", original_replace)
    recovered = MemoryStore(directory, frozenset({MemoryKind.USER}))
    async with recovered.locked():
        recovered.recover_locked()
        index = recovered.read_index_locked()

    if replace_failure == 1:
        assert index == ""
        assert not (directory / f"{USER_ID}.md").exists()
    else:
        assert "Preferred language" in index
        assert (directory / f"{USER_ID}.md").exists()
    assert not (directory / ".memory-transaction.json").exists()
    assert list(directory.glob("*.tmp")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "journal",
    [
        "{broken",
        json.dumps({"version": 1, "txn_id": "x", "writes": [], "deletes": ["../escape.md"]}),
    ],
)
async def test_invalid_official_journal_requires_manual_recovery_and_preserves_files(
    tmp_path: Path, journal: str
) -> None:
    directory = tmp_path / "memory"
    directory.mkdir()
    note = directory / f"{USER_ID}.md"
    note.write_text("preserve", encoding="utf-8")
    official = directory / ".memory-transaction.json"
    official.write_text(journal, encoding="utf-8")
    temporary = directory / ".memory-txn-x-note.tmp"
    temporary.write_text("temporary", encoding="utf-8")
    store = MemoryStore(directory, frozenset({MemoryKind.USER}))

    async with store.locked():
        with pytest.raises(MemoryRecoveryRequired):
            store.recover_locked()
        with pytest.raises(MemoryRecoveryRequired):
            store.apply_locked([_create()])

    assert (
        official.exists() and temporary.exists() and note.read_text(encoding="utf-8") == "preserve"
    )


@pytest.mark.asyncio
async def test_process_lock_does_not_steal_live_pid_and_recovers_dead_pid(tmp_path: Path) -> None:
    directory = tmp_path / "memory"
    directory.mkdir()
    lock_path = directory / ".memory-write.lock"
    lock_path.write_text(str(os.getpid()), encoding="ascii")
    store = MemoryStore(directory, frozenset({MemoryKind.USER}))

    with pytest.raises(MemoryLockError):
        async with store.locked():
            pass

    lock_path.write_text("99999999", encoding="ascii")
    async with store.locked():
        assert lock_path.exists()
    assert not lock_path.exists()


def test_pid_alive_recognizes_current_process_without_signalling_it() -> None:
    assert _pid_alive(os.getpid()) is True


@pytest.mark.asyncio
async def test_two_store_instances_cannot_hold_same_process_lock(tmp_path: Path) -> None:
    first = MemoryStore(tmp_path / "memory", frozenset({MemoryKind.USER}))
    second = MemoryStore(tmp_path / "memory", frozenset({MemoryKind.USER}))

    async with first.locked():
        with pytest.raises(MemoryLockError):
            async with second.locked():
                pass
