from novacode.agent import SessionRuntime
from novacode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from novacode.memory import MemoryKind, MemoryStore
from novacode.tool import Registry


def test_memory_store_list_files(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory", frozenset({MemoryKind.USER}))
    assert store.list_files() == []

    store.directory.mkdir()
    (store.directory / "z.md").write_text("z", encoding="utf-8")
    (store.directory / "MEMORY.md").write_text("index", encoding="utf-8")
    (store.directory / "ignore.txt").write_text("x", encoding="utf-8")
    assert store.list_files() == ["MEMORY.md", "z.md"]


def test_session_runtime_reset_for_new_session(tmp_path) -> None:
    first = new_session_context(str(tmp_path / "first"))
    second = new_session_context(str(tmp_path / "second"))
    runtime = SessionRuntime(
        ContentReplacementState(),
        RecoveryState(),
        CompactCircuitBreaker(),
        first,
        usage_anchor=10,
        anchor_msg_len=20,
        resume_reminder="old",
    )
    old_replacement = runtime.replacement

    runtime.reset_for_new_session(second)

    assert runtime.session is second
    assert runtime.replacement is not old_replacement
    assert runtime.usage_anchor == runtime.anchor_msg_len == 0
    assert runtime.resume_reminder == ""


def test_tool_registry_count() -> None:
    assert Registry().count() == 0
