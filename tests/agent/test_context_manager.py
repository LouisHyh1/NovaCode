from pathlib import Path

from novacode.agent.context_manager import ContextManager, SessionRuntime
from novacode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    TriggerKind,
    new_session_context,
)
from novacode.compact.token import estimate_tokens
from novacode.conversation import Conversation
from novacode.hook import DispatchResult
from novacode.hook import Event as HookEvent
from novacode.llm import Message, StreamEvent, ToolCall, ToolResult
from novacode.permission import Mode


class UnusedProvider:
    @property
    def name(self) -> str:
        return "unused"

    @property
    def model(self) -> str:
        return "unused"

    async def stream(self, request):
        raise AssertionError("Layer 1 不应调用 Provider")
        yield


class SummaryProvider:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    @property
    def name(self) -> str:
        return "summary"

    @property
    def model(self) -> str:
        return "summary"

    async def stream(self, request):
        self.order.append("provider")
        yield StreamEvent(text="保留用户目标与已完成工作。")


def _runtime(tmp_path: Path) -> SessionRuntime:
    return SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(str(tmp_path)),
    )


async def test_layer1_offload_reports_metadata_without_compact_hooks(tmp_path: Path) -> None:
    hooks: list[HookEvent] = []

    async def dispatch(event: HookEvent, mode: Mode, **values) -> DispatchResult:
        hooks.append(event)
        return DispatchResult()

    conv = Conversation()
    call = ToolCall(id="large", name="read_file", input='{"path":"large.txt"}')
    conv.add_user("读取大文件")
    conv.add_assistant_with_tool_calls("", [call])
    conv.add_tool_results([ToolResult(tool_call_id=call.id, content="x" * 60_000)])
    manager = ContextManager(
        UnusedProvider(),
        _runtime(tmp_path),
        context_window=1_000_000,
        dispatch_hook=dispatch,
    )

    result = await manager.prepare(conv, [], TriggerKind.AUTO, Mode.DEFAULT)

    assert result.offloaded is True
    assert result.summarized is False
    assert hooks == []
    assert "[tool result compacted]" in conv.messages()[-1].tool_results[0].content


async def test_summary_hooks_wrap_compaction_and_refresh_token_anchor(tmp_path: Path) -> None:
    order: list[str] = []
    runtime = _runtime(tmp_path)

    async def dispatch(event: HookEvent, mode: Mode, **values) -> DispatchResult:
        order.append(event.value)
        if event is HookEvent.POST_COMPACT:
            assert runtime.usage_anchor > 0
            assert runtime.anchor_msg_len > 0
        return DispatchResult()

    conv = Conversation()
    conv.add_user("请总结之前的工作")
    conv.add_assistant("已经完成第一步")
    manager = ContextManager(
        SummaryProvider(order),
        runtime,
        context_window=200_000,
        dispatch_hook=dispatch,
    )

    result = await manager.prepare(conv, [], TriggerKind.MANUAL, Mode.DEFAULT)

    assert result.summarized is True
    assert order == [HookEvent.PRE_COMPACT.value, "provider", HookEvent.POST_COMPACT.value]
    assert runtime.usage_anchor == result.after_tokens
    assert runtime.anchor_msg_len == conv.length()


async def test_auto_summary_dispatches_pre_hook_before_provider(tmp_path: Path) -> None:
    order: list[str] = []

    async def dispatch(event: HookEvent, mode: Mode, **values) -> DispatchResult:
        order.append(event.value)
        return DispatchResult()

    conv = Conversation()
    conv.add_user("需要自动压缩的历史")
    conv.add_assistant("历史内容")
    manager = ContextManager(
        SummaryProvider(order),
        _runtime(tmp_path),
        context_window=33_000,
        dispatch_hook=dispatch,
    )

    result = await manager.prepare(conv, [], TriggerKind.AUTO, Mode.DEFAULT)

    assert result.summarized is True
    assert order == [HookEvent.PRE_COMPACT.value, "provider", HookEvent.POST_COMPACT.value]


async def test_summary_anchor_counts_new_assistant_message_once(tmp_path: Path) -> None:
    async def dispatch(event: HookEvent, mode: Mode, **values) -> DispatchResult:
        return DispatchResult()

    conv = Conversation()
    conv.add_user("旧历史")
    conv.add_assistant("旧回复")
    manager = ContextManager(
        SummaryProvider([]),
        _runtime(tmp_path),
        context_window=200_000,
        dispatch_hook=dispatch,
    )
    await manager.prepare(conv, [], TriggerKind.MANUAL, Mode.DEFAULT)
    anchored = manager.estimate(conv)

    conv.add_assistant("新增回复")

    delta = estimate_tokens(0, [Message(role="assistant", content="新增回复")], 0)
    assert manager.estimate(conv) == anchored + delta


async def test_emergency_summary_keeps_recovery_snapshot(tmp_path: Path) -> None:
    async def dispatch(event: HookEvent, mode: Mode, **values) -> DispatchResult:
        return DispatchResult()

    runtime = _runtime(tmp_path)
    source = tmp_path / "source.py"
    runtime.recovery.record_file(str(source), "important = True")
    conv = Conversation()
    conv.add_user("恢复溢出的上下文")
    manager = ContextManager(
        SummaryProvider([]),
        runtime,
        context_window=200_000,
        dispatch_hook=dispatch,
    )

    result = await manager.prepare(conv, [], TriggerKind.EMERGENCY, Mode.DEFAULT)

    assert result.summarized is True
    summary = conv.messages()[0].content
    assert str(source) in summary
    assert "important = True" in summary
