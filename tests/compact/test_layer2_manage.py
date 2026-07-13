from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from novacode.compact.compact import ManageInput, TriggerKind, manage_context
from novacode.compact.const import auto_compact_threshold
from novacode.compact.layer2 import (
    COMPACT_SUMMARY_MARKER,
    group_by_user_turn,
    is_compact_summary,
    pick_recent_tail,
)
from novacode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
)
from novacode.conversation import Conversation
from novacode.llm import Message, Request, StreamEvent, ToolCall, ToolDefinition, ToolResult


class SummaryProvider:
    def __init__(self, text: str = "<summary>summary text</summary>") -> None:
        self.requests: list[Request] = []
        self.text = text

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        self.requests.append(req)
        yield StreamEvent(text=self.text)
        yield StreamEvent(done=True)


def _input(tmp_path: Path, conv: Conversation, provider: SummaryProvider, trigger: TriggerKind):
    return ManageInput(
        conv=conv,
        provider=provider,
        model="fake-model",
        context_window=200_000,
        tool_defs=[ToolDefinition("read_file", "Read", {"type": "object"})],
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=SessionContext("s", str(tmp_path)),
        usage_anchor=0,
        anchor_msg_len=0,
        estimated_token=1000,
        trigger=trigger,
    )


@pytest.mark.asyncio
async def test_manual_manage_context_replaces_conversation(tmp_path: Path) -> None:
    conv = Conversation()
    conv.add_user("first")
    conv.add_assistant("second")
    provider = SummaryProvider()

    out = await manage_context(_input(tmp_path, conv, provider, TriggerKind.MANUAL))

    msgs = conv.messages()
    assert len(provider.requests) == 1
    assert provider.requests[0].tools == []
    assert out.before_tokens == 1000
    assert out.after_tokens >= 0
    assert msgs[0].role == "user"
    assert "summary text" in msgs[0].content
    assert "当前可用工具" in msgs[0].content


def test_auto_compact_threshold_uses_current_context_window() -> None:
    assert auto_compact_threshold(200_000) == 167_000
    assert auto_compact_threshold(128_000) == 95_000
    assert auto_compact_threshold(100_000) == 67_000


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize("context_window", [200_000, 128_000, 100_000])
async def test_auto_compact_uses_layer1_estimate_and_current_window(
    tmp_path: Path, context_window: int
) -> None:
    threshold = auto_compact_threshold(context_window)

    low_conv = Conversation()
    low_conv.add_user("below")
    low_provider = SummaryProvider()
    low = _input(tmp_path, low_conv, low_provider, TriggerKind.AUTO)
    low.context_window = context_window
    low.usage_anchor = threshold - 1
    low.anchor_msg_len = low_conv.length()
    low.estimated_token = threshold - 1

    low_out = await manage_context(low)

    assert low_provider.requests == []
    assert low_out.after_tokens == threshold - 1

    high_conv = Conversation()
    high_conv.add_user("at threshold")
    high_provider = SummaryProvider()
    high = _input(tmp_path, high_conv, high_provider, TriggerKind.AUTO)
    high.context_window = context_window
    high.usage_anchor = threshold
    high.anchor_msg_len = high_conv.length()
    high.estimated_token = threshold

    high_out = await manage_context(high)

    assert len(high_provider.requests) == 1
    assert high_out.before_tokens == threshold


@pytest.mark.asyncio
async def test_auto_compact_does_not_write_back_when_summary_is_larger(
    tmp_path: Path,
) -> None:
    conv = Conversation()
    conv.add_user("keep this exact history")
    provider = SummaryProvider("<summary>" + ("x" * 600_000) + "</summary>")
    in_ = _input(tmp_path, conv, provider, TriggerKind.AUTO)
    threshold = auto_compact_threshold(in_.context_window)
    in_.usage_anchor = threshold
    in_.anchor_msg_len = conv.length()
    in_.estimated_token = threshold

    out = await manage_context(in_)

    assert out.after_tokens > out.before_tokens
    msgs = conv.messages()
    assert len(msgs) == 1
    assert msgs[0].content == "keep this exact history"


@pytest.mark.asyncio
async def test_repeated_compact_does_not_stack_old_summaries(tmp_path: Path) -> None:
    conv = Conversation()
    conv.add_user("分析项目结构")
    provider = SummaryProvider()

    for _ in range(3):
        await manage_context(_input(tmp_path, conv, provider, TriggerKind.MANUAL))

    summaries = [m for m in conv.messages() if is_compact_summary(m)]
    assert len(summaries) == 1
    assert sum(COMPACT_SUMMARY_MARKER in m.content for m in conv.messages()) == 1


def test_group_by_user_turn_starts_groups_at_user() -> None:
    msgs = [
        Message(role="user", content="u1"),
        Message(role="assistant", content="a1"),
        Message(role="tool", tool_results=[ToolResult("t1", "r")]),
        Message(role="user", content="u2"),
    ]

    groups = group_by_user_turn(msgs)

    assert [[m.content for m in g if m.content] for g in groups] == [["u1", "a1"], ["u2"]]


def test_pick_recent_tail_does_not_cut_tool_pair() -> None:
    msgs = [
        Message(role="user", content="u"),
        Message(
            role="assistant",
            content="a",
            tool_calls=[ToolCall(id="t1", name="read_file", input="{}")],
        ),
        Message(role="tool", tool_results=[ToolResult("t1", "r")]),
    ]

    tail = pick_recent_tail(msgs)

    assert tail[0].role == "user"
    assert [m.role for m in tail] == ["user", "assistant", "tool"]


def test_pick_recent_tail_skips_old_compact_summary() -> None:
    msgs = [
        Message(role="user", content=f"{COMPACT_SUMMARY_MARKER}\nold summary"),
        Message(role="user", content="current user message"),
    ]

    tail = pick_recent_tail(msgs)

    assert [m.content for m in tail] == ["current user message"]
