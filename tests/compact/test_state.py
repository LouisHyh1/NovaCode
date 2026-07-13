from pathlib import Path

from novacode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)


def test_session_context_creates_spill_dir(tmp_path: Path) -> None:
    session = new_session_context(str(tmp_path))

    assert session.session_id
    assert Path(session.spill_dir).is_dir()
    assert Path(session.spill_dir).parent.name == session.session_id


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


def test_recovery_state_snapshot_is_sorted_copy(tmp_path: Path) -> None:
    recovery = RecoveryState()
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"

    recovery.record_file(str(a), "old")
    recovery.record_file(str(b), "new")
    records = recovery.snapshot()
    records[0].content = "mutated"

    fresh = recovery.snapshot()
    assert [Path(r.path).name for r in fresh] == ["b.txt", "a.txt"]
    assert fresh[0].content == "new"
