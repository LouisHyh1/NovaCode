import re
from datetime import datetime
from pathlib import Path

import pytest

from novacode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
    new_session_id,
    open_session_context,
)


def test_session_context_creates_spill_dir(tmp_path: Path) -> None:
    session = new_session_context(str(tmp_path))

    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", session.session_id)
    assert Path(session.message_path) == (
        tmp_path / ".novacode" / "sessions" / f"{session.session_id}.jsonl"
    )
    assert not Path(session.message_path).exists()
    assert Path(session.spill_dir).is_dir()
    assert Path(session.spill_dir) == (
        tmp_path / ".novacode" / "sessions" / session.session_id / "tool-results"
    )


def test_new_session_id_uses_local_time_and_random_hex_suffix() -> None:
    session_id = new_session_id(datetime(2026, 7, 20, 9, 8, 7))

    assert re.fullmatch(r"20260720-090807-[0-9a-f]{4}", session_id)


def test_open_session_context_reuses_existing_paths(tmp_path: Path) -> None:
    session_id = "20260720-090807-abcd"
    sessions_dir = tmp_path / ".novacode" / "sessions"
    sessions_dir.mkdir(parents=True)
    message_path = sessions_dir / f"{session_id}.jsonl"
    message_path.write_text("{}\n", encoding="utf-8")

    session = open_session_context(str(tmp_path), session_id)

    assert Path(session.message_path) == message_path
    assert Path(session.spill_dir) == sessions_dir / session_id / "tool-results"
    assert Path(session.spill_dir).is_dir()


@pytest.mark.parametrize("session_id", ["bad", "20260720-090807-XYZ1", "../session"])
def test_open_session_context_rejects_invalid_id(tmp_path: Path, session_id: str) -> None:
    with pytest.raises(ValueError, match="session ID"):
        open_session_context(str(tmp_path), session_id)


def test_open_session_context_requires_message_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_session_context(str(tmp_path), "20260720-090807-abcd")


def test_replacement_state_freezes_decision() -> None:
    state = ContentReplacementState()
    calls = 0

    def decide() -> tuple[str, str]:
        nonlocal calls
        calls += 1
        return "replaced", f"preview-{calls}"

    assert state.decide_once("t1", "original", decide) == "preview-1"
    assert state.decide_once("t1", "new original", decide) == "preview-1"
    assert calls == 1


def test_replacement_state_kept_and_skip_paths() -> None:
    state = ContentReplacementState()
    kept_calls = 0

    def keep() -> tuple[str, str]:
        nonlocal kept_calls
        kept_calls += 1
        return "kept", ""

    assert state.decide_once("keep", "original", keep) == "original"
    assert state.decide_once("keep", "changed", keep) == "changed"
    assert kept_calls == 1

    skip_calls = 0

    def skip() -> tuple[str, str]:
        nonlocal skip_calls
        skip_calls += 1
        return "skip", ""

    assert state.decide_once("skip", "a", skip) == "a"
    assert state.decide_once("skip", "b", skip) == "b"
    assert skip_calls == 2


def test_circuit_breaker_trips_after_three_failures_and_resets() -> None:
    breaker = CompactCircuitBreaker()

    breaker.record_failure()
    breaker.record_failure()
    assert breaker.tripped() is False
    breaker.record_success()
    assert breaker.tripped() is False
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.tripped() is True


def test_recovery_state_snapshot_is_sorted_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovery = RecoveryState()
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    timestamp = datetime(2026, 7, 20, 9, 8, 7)

    class FrozenDateTime:
        @staticmethod
        def now(_tz):
            return timestamp

    monkeypatch.setattr("novacode.compact.state.datetime", FrozenDateTime)

    recovery.record_file(str(a), "old")
    recovery.record_file(str(b), "new")
    records = recovery.snapshot()
    records[0].content = "mutated"

    fresh = recovery.snapshot()
    assert [Path(r.path).name for r in fresh] == ["b.txt", "a.txt"]
    assert fresh[0].content == "new"

    recovery.record_file(str(a), "latest")
    reread = recovery.snapshot()
    assert [Path(r.path).name for r in reread] == ["a.txt", "b.txt"]
    assert reread[0].content == "latest"
