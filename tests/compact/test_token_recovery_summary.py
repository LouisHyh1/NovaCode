from datetime import datetime

from novacode.compact.recovery import BOUNDARY_NOTICE, build_recovery_attachment
from novacode.compact.state import FileReadRecord
from novacode.compact.summary_prompt import build_summary_prompt, extract_summary
from novacode.compact.token import estimate_tokens, usage_anchor
from novacode.llm import Message, ToolCall, ToolDefinition, ToolResult, Usage


def test_estimate_tokens_uses_anchor_and_message_delta() -> None:
    msgs = [
        Message(role="user", content="already counted"),
        Message(role="assistant", content="x" * 35),
    ]

    assert estimate_tokens(100, msgs, 1) == 110


def test_usage_anchor_sums_usage_fields() -> None:
    assert usage_anchor(Usage(1, 2, 3, 4)) == 10


def test_usage_anchor_prefers_provider_context_tokens() -> None:
    assert usage_anchor(Usage(1, 2, 3, 4, context_tokens=99)) == 99


def test_recovery_attachment_contains_files_tools_and_boundary() -> None:
    files = [FileReadRecord(path="/tmp/a.py", content="print(1)", timestamp=datetime(2026, 1, 1))]
    tools = [
        ToolDefinition(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object"},
        )
    ]

    text = build_recovery_attachment(files, tools)

    assert "最近读过的文件" in text
    assert "/tmp/a.py" in text
    assert "read_file" in text
    assert BOUNDARY_NOTICE in text


def test_summary_prompt_and_extract_summary() -> None:
    msgs = [
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            content="tool",
            tool_calls=[ToolCall(id="t1", name="read_file", input='{"path":"a"}')],
        ),
        Message(role="tool", tool_results=[ToolResult(tool_call_id="t1", content="data")]),
    ]

    prompt = build_summary_prompt(msgs)
    assert len(prompt) == 1
    assert "## 8 当前工作" in prompt[0].content
    assert "[call read_file id=t1" in prompt[0].content
    assert extract_summary("<analysis>x</analysis><summary>final</summary>") == "final"
