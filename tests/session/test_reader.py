import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from novacode.llm import Message, ToolCall, ToolResult
from novacode.session import SessionWriter, load_session

SESSION_ID = "20260720-090807-abcd"


def test_reader_skips_bad_and_unknown_lines_then_continues(tmp_path: Path) -> None:
    path = tmp_path / f"{SESSION_ID}.jsonl"
    records = [
        {
            "type": "message",
            "role": "user",
            "content": "first",
            "model": "model-a",
            "ts": datetime(2026, 7, 20, tzinfo=UTC).isoformat(),
        },
        {"type": "unknown", "ts": datetime(2026, 7, 20, tzinfo=UTC).isoformat()},
        {
            "type": "message",
            "role": "assistant",
            "content": "last",
            "ts": datetime(2026, 7, 20, 1, tzinfo=UTC).isoformat(),
        },
    ]
    path.write_text(
        json.dumps(records[0])
        + "\n{broken\n"
        + "\n".join(json.dumps(r) for r in records[1:])
        + "\n",
        encoding="utf-8",
    )

    result = load_session(path)

    assert result.model == "model-a"
    assert [message.content for message in result.messages] == ["first", "last"]
    assert len(result.diagnostics) == 2


def _write_messages(tmp_path: Path, messages: list[Message]) -> Path:
    writer = SessionWriter(tmp_path, SESSION_ID, "model-a")
    for message in messages:
        writer.append_message(message)
    writer.close()
    return writer.path


def test_reader_preserves_complete_tool_chain(tmp_path: Path) -> None:
    path = _write_messages(
        tmp_path,
        [
            Message(role="user", content="read"),
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="a", name="read_file", input="{}"),
                    ToolCall(id="b", name="glob", input="{}"),
                ],
            ),
            Message(
                role="tool",
                tool_results=[ToolResult("a", "A"), ToolResult("b", "B")],
            ),
            Message(role="assistant", content="done"),
        ],
    )

    assert [message.role for message in load_session(path).messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


@pytest.mark.parametrize(
    "tail",
    [
        [Message(role="assistant", tool_calls=[ToolCall("a", "read_file", "{}")])],
        [
            Message(
                role="assistant",
                tool_calls=[ToolCall("a", "read_file", "{}"), ToolCall("b", "glob", "{}")],
            ),
            Message(role="tool", tool_results=[ToolResult("a", "A")]),
        ],
        [
            Message(
                role="assistant",
                tool_calls=[ToolCall("a", "read_file", "{}"), ToolCall("b", "glob", "{}")],
            ),
            Message(role="tool", tool_results=[ToolResult("b", "B"), ToolResult("a", "A")]),
        ],
    ],
)
def test_reader_truncates_before_incomplete_or_mismatched_tool_chain(
    tmp_path: Path, tail: list[Message]
) -> None:
    path = _write_messages(tmp_path, [Message(role="user", content="keep"), *tail])

    result = load_session(path)

    assert [message.content for message in result.messages] == ["keep"]
    assert any("tool chain" in diagnostic for diagnostic in result.diagnostics)


def test_reader_truncates_before_orphan_tool_result(tmp_path: Path) -> None:
    path = _write_messages(
        tmp_path,
        [
            Message(role="user", content="keep"),
            Message(role="tool", tool_results=[ToolResult("orphan", "result")]),
            Message(role="assistant", content="discard"),
        ],
    )

    result = load_session(path)

    assert [message.content for message in result.messages] == ["keep"]
    assert any("orphan tool results" in diagnostic for diagnostic in result.diagnostics)
