from pathlib import Path

from novacode.compact.layer1 import build_preview, offload_and_snip, spill_single
from novacode.compact.state import ContentReplacementState, SessionContext
from novacode.llm import Message, ToolResult


def test_spill_single_is_idempotent(tmp_path: Path) -> None:
    session = SessionContext(
        session_id="s", message_path=str(tmp_path / "s.jsonl"), spill_dir=str(tmp_path)
    )

    spill_single(session, "tool1", "first")
    spill_single(session, "tool1", "second")

    assert (tmp_path / "tool1").read_text(encoding="utf-8") == "first"


def test_build_preview_contains_required_fields(tmp_path: Path) -> None:
    preview = build_preview(80000, "head text", str(tmp_path / "tool1"))

    assert "original size:" in preview
    assert "80000" in preview
    assert "head preview" in preview
    assert "[saved to]" in preview
    assert "文件读取工具" in preview
    assert "不要凭头部预览猜测" in preview


def test_offload_and_snip_replaces_large_result_and_freezes_preview(tmp_path: Path) -> None:
    session = SessionContext(
        session_id="s", message_path=str(tmp_path / "s.jsonl"), spill_dir=str(tmp_path)
    )
    state = ContentReplacementState()
    large = "x" * 60000
    msgs = [Message(role="tool", tool_results=[ToolResult(tool_call_id="t1", content=large)])]

    first = offload_and_snip(msgs, state, session)
    second = offload_and_snip(
        [Message(role="tool", tool_results=[ToolResult(tool_call_id="t1", content="changed")])],
        state,
        session,
    )

    assert first[0].tool_results[0].content != large
    assert first[0].tool_results[0].content == second[0].tool_results[0].content
    assert (tmp_path / "t1").stat().st_size == 60000


def test_offload_and_snip_enforces_aggregate_limit(tmp_path: Path) -> None:
    session = SessionContext(
        session_id="s", message_path=str(tmp_path / "s.jsonl"), spill_dir=str(tmp_path)
    )
    state = ContentReplacementState()
    results = [ToolResult(tool_call_id=f"t{i}", content=str(i) * 80000) for i in range(3)]
    msgs = [Message(role="tool", tool_results=results)]

    updated = offload_and_snip(msgs, state, session)
    total = sum(len(r.content.encode("utf-8")) for r in updated[0].tool_results)

    assert total <= 200000
    assert len(list(tmp_path.iterdir())) >= 1
