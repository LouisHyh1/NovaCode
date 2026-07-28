import asyncio
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import novacode.session.cleanup as cleanup_module
from novacode.session import clean_expired, clean_expired_async, list_sessions


def _write_message(path: Path, content: str, ts: datetime, model: str = "model-a") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "message",
        "role": "user",
        "content": content,
        "model": model,
        "ts": ts.isoformat(),
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def test_list_sessions_uses_record_activity_not_file_mtime(tmp_path: Path) -> None:
    older = tmp_path / "20260720-090807-abcd.jsonl"
    newer = tmp_path / "20260720-100807-bcde.jsonl"
    _write_message(older, "old title\nsecond line", datetime(2026, 7, 19, tzinfo=UTC))
    _write_message(newer, "new title", datetime(2026, 7, 20, tzinfo=UTC))
    os.utime(older, (2_000_000_000, 2_000_000_000))
    os.utime(newer, (1_000_000_000, 1_000_000_000))
    (tmp_path / "20260720-110807-cdef.jsonl").write_text("{bad", encoding="utf-8")

    sessions = list_sessions(tmp_path)

    assert [session.session_id for session in sessions] == [newer.stem, older.stem]
    assert sessions[1].title == "old title second line"
    assert sessions[0].model == "model-a"
    assert sessions[0].file_size == newer.stat().st_size


def test_list_sessions_uses_fallback_title_without_user_message(tmp_path: Path) -> None:
    path = tmp_path / "20260720-090807-abcd.jsonl"
    record = {
        "type": "message",
        "role": "assistant",
        "content": "hello",
        "model": "model-a",
        "ts": datetime(2026, 7, 20, tzinfo=UTC).isoformat(),
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert list_sessions(tmp_path)[0].title == "（无用户消息）"


def test_clean_expired_uses_valid_record_activity_and_strict_boundary(tmp_path: Path) -> None:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    keep = tmp_path / "20260620-100807-abcd.jsonl"
    remove = tmp_path / "20260620-080707-bcde.jsonl"
    _write_message(keep, "keep", now - timedelta(days=29, hours=23))
    _write_message(remove, "remove", now - timedelta(days=30, hours=1))
    keep_tools = tmp_path / keep.stem / "tool-results"
    remove_tools = tmp_path / remove.stem / "tool-results"
    keep_tools.mkdir(parents=True)
    remove_tools.mkdir(parents=True)

    clean_expired(tmp_path, now)

    assert keep.exists() and keep_tools.exists()
    assert not remove.exists() and not remove_tools.exists()


def test_clean_expired_invalid_file_uses_latest_file_or_tool_directory_mtime(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    path = tmp_path / "20260601-090807-abcd.jsonl"
    path.write_text("{bad", encoding="utf-8")
    tools = tmp_path / path.stem / "tool-results"
    tools.mkdir(parents=True)
    old = (now - timedelta(days=40)).timestamp()
    recent = (now - timedelta(days=1)).timestamp()
    os.utime(path, (old, old))
    os.utime(tools, (recent, recent))

    clean_expired(tmp_path, now)
    assert path.exists()

    os.utime(tools, (old, old))
    clean_expired(tmp_path, now)
    assert not path.exists()
    assert not tools.exists()


@pytest.mark.asyncio
async def test_clean_expired_async_runs_sync_worker_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called_from: list[int] = []

    def fake_clean(*args, **kwargs) -> None:
        called_from.append(threading.get_ident())

    monkeypatch.setattr(cleanup_module, "clean_expired", fake_clean)

    await clean_expired_async(tmp_path, datetime.now(UTC))

    assert called_from and called_from[0] != threading.get_ident()
    await asyncio.sleep(0)


def test_clean_expired_isolates_single_delete_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    blocked = tmp_path / "20260601-090807-abcd.jsonl"
    removable = tmp_path / "20260601-100807-bcde.jsonl"
    _write_message(blocked, "blocked", now - timedelta(days=40))
    _write_message(removable, "remove", now - timedelta(days=40))
    original_unlink = Path.unlink

    def selective_unlink(path: Path, *args, **kwargs) -> None:
        if path == blocked:
            raise OSError("blocked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", selective_unlink)

    clean_expired(tmp_path, now)

    assert blocked.exists()
    assert not removable.exists()
