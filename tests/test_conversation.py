"""Tests for conversation module."""

import pytest

from novacode.conversation import Conversation
from novacode.llm import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER, Message, ToolCall, ToolResult


def test_add_and_retrieve() -> None:
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi there")
    msgs = conv.messages()
    assert len(msgs) == 2
    assert msgs[0].role == ROLE_USER
    assert msgs[0].content == "hello"
    assert msgs[1].role == ROLE_ASSISTANT
    assert msgs[1].content == "hi there"


def test_messages_is_copy() -> None:
    conv = Conversation()
    conv.add_user("hello")
    msgs = conv.messages()
    msgs.clear()
    assert len(conv.messages()) == 1


def test_messages_deep_copies_nested_tool_data() -> None:
    conv = Conversation()
    conv.add_assistant_with_tool_calls(
        "call",
        [ToolCall(id="t1", name="read_file", input='{"path":"a"}')],
    )

    msgs = conv.messages()
    msgs[0].tool_calls[0].name = "mutated"

    assert conv.messages()[0].tool_calls[0].name == "read_file"


def test_replace_history_deep_copies_and_rejects_none() -> None:
    conv = Conversation()
    msgs = [ToolResult(tool_call_id="t1", content="result")]
    replacement = [Message(role=ROLE_TOOL, tool_results=msgs)]

    conv.replace_history(replacement)
    replacement[0].tool_results[0].content = "mutated"

    assert conv.messages()[0].tool_results[0].content == "result"

    with pytest.raises(TypeError):
        conv.replace_history(None)  # type: ignore[arg-type]


def test_tool_call_roundtrip() -> None:
    """依次 add_user → add_assistant_with_tool_calls → add_tool_results → add_assistant，
    检查 messages() 长度=4、role 序列正确、tool_calls/tool_results 内容正确。"""
    conv = Conversation()
    conv.add_user("read test.txt")
    calls = [ToolCall(id="toolu_001", name="read_file", input='{"path": "test.txt"}')]
    conv.add_assistant_with_tool_calls("Let me read the file.", calls)
    results = [ToolResult(tool_call_id="toolu_001", content="hello", is_error=False)]
    conv.add_tool_results(results)
    conv.add_assistant("The file contains 'hello'.")

    msgs = conv.messages()
    assert len(msgs) == 4
    assert msgs[0].role == ROLE_USER
    assert msgs[1].role == ROLE_ASSISTANT
    assert len(msgs[1].tool_calls) == 1
    assert msgs[1].tool_calls[0].name == "read_file"
    assert msgs[2].role == ROLE_TOOL
    assert len(msgs[2].tool_results) == 1
    assert msgs[2].tool_results[0].content == "hello"
    assert msgs[3].role == ROLE_ASSISTANT
    assert "hello" in msgs[3].content


def test_last_role_empty() -> None:
    """空会话 last_role() 返回空字符串。"""
    conv = Conversation()
    assert conv.last_role() == ""


def test_last_role_user() -> None:
    """add_user 后 last_role() == 'user'。"""
    conv = Conversation()
    conv.add_user("hello")
    assert conv.last_role() == ROLE_USER


def test_last_role_assistant() -> None:
    """add_assistant 后 last_role() == 'assistant'。"""
    conv = Conversation()
    conv.add_assistant("hi")
    assert conv.last_role() == ROLE_ASSISTANT


def test_last_role_tool() -> None:
    """add_tool_results 后 last_role() == 'tool'。"""
    conv = Conversation()
    conv.add_tool_results([ToolResult(tool_call_id="t1", content="ok")])
    assert conv.last_role() == ROLE_TOOL


def test_before_append_runs_before_memory_change_and_can_reject() -> None:
    observed_lengths: list[int] = []
    conv: Conversation

    def before_append(message: Message) -> None:
        observed_lengths.append(conv.length())
        if message.content == "reject":
            raise OSError("disk failed")

    conv = Conversation(before_append=before_append)
    conv.add_user("accepted")

    with pytest.raises(OSError, match="disk failed"):
        conv.add_assistant("reject")

    assert observed_lengths == [0, 1]
    assert [message.content for message in conv.messages()] == ["accepted"]


def test_before_replace_runs_before_memory_change_and_can_reject() -> None:
    conv = Conversation(before_replace=lambda _: (_ for _ in ()).throw(OSError("compact failed")))
    conv.add_user("original")

    with pytest.raises(OSError, match="compact failed"):
        conv.replace_history([Message(role=ROLE_USER, content="replacement")])

    assert [message.content for message in conv.messages()] == ["original"]


def test_from_messages_deep_copies_without_replaying_hooks() -> None:
    calls: list[str] = []
    source = [Message(role=ROLE_USER, content="restored")]

    conv = Conversation.from_messages(
        source, before_append=lambda message: calls.append(message.role)
    )
    source[0].content = "mutated"

    assert calls == []
    assert conv.messages()[0].content == "restored"
    conv.add_assistant("new")
    assert calls == [ROLE_ASSISTANT]
