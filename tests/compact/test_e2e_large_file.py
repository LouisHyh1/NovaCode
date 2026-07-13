"""端到端测试：使用项目中的大文件验证上下文压缩完整流程。

覆盖：
- Layer 1：大工具结果 spill + preview 替换
- Layer 2：AUTO/MANUAL/EMERGENCY 三种触发模式
- Recovery：文件快照在压缩后保留
- Token 估算：压缩前后 token 数变化
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from novacode.compact.compact import ManageInput, TriggerKind, manage_context
from novacode.compact.const import AUTO_COMPACT_TRIGGER_TOKENS, SINGLE_RESULT_LIMIT
from novacode.compact.layer2 import is_compact_summary
from novacode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from novacode.compact.token import estimate_tokens
from novacode.conversation import Conversation
from novacode.llm import (
    Request,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    ToolResult,
    Usage,
)


class EchoSummaryProvider:
    """模拟 LLM Provider：返回固定摘要文本。"""

    def __init__(self, summary: str = "这是对话历史的压缩摘要。") -> None:
        self.summary = summary
        self.request_count = 0

    @property
    def name(self) -> str:
        return "echo"

    @property
    def model(self) -> str:
        return "echo-model"

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        self.request_count += 1
        text = f"<analysis>分析中...</analysis>\n<summary>{self.summary}</summary>"
        yield StreamEvent(text=text)
        yield StreamEvent(usage=Usage(input_tokens=100, output_tokens=50))
        yield StreamEvent(done=True)


def _read_large_file() -> str:
    """读取项目中的大文件作为测试素材。"""
    candidates = [
        Path(__file__).parent.parent.parent / "docs" / "ch08" / "上下文管理 Tasks.md",
        Path(__file__).parent.parent.parent / "docs" / "ch08" / "上下文管理 Plan.md",
        Path(__file__).parent.parent.parent / "tests" / "test_agent.py",
        Path(__file__).parent.parent.parent / "src" / "novacode" / "agent" / "__init__.py",
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 10_000:
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("未找到足够大的测试文件")


def _make_input(
    tmp_path: Path,
    conv: Conversation,
    provider: EchoSummaryProvider,
    trigger: TriggerKind,
    estimated: int = 0,
) -> ManageInput:
    tool_defs = [
        ToolDefinition(
            "read_file",
            "Read a file",
            {"type": "object", "properties": {"path": {"type": "string"}}},
        ),
        ToolDefinition(
            "bash",
            "Run shell command",
            {"type": "object", "properties": {"command": {"type": "string"}}},
        ),
    ]
    return ManageInput(
        conv=conv,
        provider=provider,
        model="echo-model",
        context_window=200_000,
        tool_defs=tool_defs,
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(str(tmp_path)),
        usage_anchor=0,
        anchor_msg_len=0,
        estimated_token=estimated or AUTO_COMPACT_TRIGGER_TOKENS,
        trigger=trigger,
    )


# ── Layer 1：大工具结果 spill ──────────────────────────────────


@pytest.mark.asyncio
async def test_layer1_spill_large_tool_result(tmp_path: Path) -> None:
    """模拟 read_file 返回大文件内容，验证 Layer 1 自动 spill 并替换为 preview。"""
    large_content = _read_large_file()
    assert len(large_content.encode("utf-8")) > SINGLE_RESULT_LIMIT, "测试文件需要足够大"

    conv = Conversation()
    conv.add_user("请读取这个大文件")
    conv.add_assistant_with_tool_calls(
        "", [ToolCall(id="call_1", name="read_file", input='{"path": "large.md"}')]
    )
    conv.add_tool_results([ToolResult(tool_call_id="call_1", content=large_content)])

    provider = EchoSummaryProvider()
    in_ = _make_input(tmp_path, conv, provider, TriggerKind.MANUAL)

    out = await manage_context(in_)

    msgs = conv.messages()
    tool_msg = [m for m in msgs if m.role == "tool"][0]
    replaced_content = tool_msg.tool_results[0].content

    assert "tool result compacted" in replaced_content
    assert "head preview" in replaced_content
    assert len(replaced_content.encode("utf-8")) < SINGLE_RESULT_LIMIT

    spill_dir = Path(in_.session.spill_dir)
    spill_files = list(spill_dir.iterdir())
    assert len(spill_files) >= 1
    spilled = spill_files[0].read_text(encoding="utf-8")
    assert spilled == large_content

    assert out.after_tokens <= out.before_tokens


# ── Layer 2 MANUAL：手动 /compact 命令 ────────────────────────


@pytest.mark.asyncio
async def test_manual_compact_with_large_history(tmp_path: Path) -> None:
    """构建大量对话历史（含大文件工具结果），手动触发压缩。"""
    large_content = _read_large_file()

    conv = Conversation()
    for i in range(10):
        conv.add_user(f"第 {i + 1} 轮请求：请分析代码")
        conv.add_assistant(f"第 {i + 1} 轮分析结果：这是一个重要的代码段...")
        if i % 3 == 0:
            conv.add_assistant_with_tool_calls(
                "", [ToolCall(id=f"call_{i}", name="read_file", input=f'{{"path": "file_{i}.py"}}')]
            )
            content = large_content if i % 6 == 0 else f"小型结果 {i}" * 100
            conv.add_tool_results([ToolResult(tool_call_id=f"call_{i}", content=content)])

    before_tokens = estimate_tokens(0, conv.messages(), 0)

    provider = EchoSummaryProvider("压缩摘要：用户多次请求分析代码，涉及多个文件的读取和分析。")
    in_ = _make_input(tmp_path, conv, provider, TriggerKind.MANUAL, estimated=before_tokens)

    out = await manage_context(in_)

    assert provider.request_count == 1
    assert out.before_tokens > 0
    assert out.after_tokens > 0

    msgs = conv.messages()
    summaries = [m for m in msgs if is_compact_summary(m)]
    assert len(summaries) == 1
    assert "压缩摘要" in summaries[0].content
    assert "当前可用工具" in summaries[0].content


# ── Layer 2 AUTO：自动压缩触发 ─────────────────────────────────


@pytest.mark.asyncio
async def test_auto_single_large_tool_result_uses_layer1_without_layer2(tmp_path: Path) -> None:
    """单个项目大文件被 Layer1 preview 后低于阈值，不应再触发 Layer2。"""
    large_content = _read_large_file()

    conv = Conversation()
    conv.add_user("分析这个大文件的内容")
    conv.add_assistant_with_tool_calls(
        "", [ToolCall(id="c1", name="read_file", input='{"path": "big.py"}')]
    )
    conv.add_tool_results([ToolResult(tool_call_id="c1", content=large_content)])
    conv.add_assistant("分析完成，以下是关键点...")

    provider = EchoSummaryProvider()
    before = estimate_tokens(0, conv.messages(), 0)
    in_ = _make_input(tmp_path, conv, provider, TriggerKind.AUTO, estimated=before)

    out = await manage_context(in_)

    assert provider.request_count == 0
    assert out.after_tokens < out.before_tokens
    msgs = conv.messages()
    assert not [m for m in msgs if is_compact_summary(m)]
    tool_msg = [m for m in msgs if m.role == "tool"][0]
    assert "tool result compacted" in tool_msg.tool_results[0].content


@pytest.mark.asyncio
async def test_auto_compact_triggers_when_layer1_result_still_exceeds_threshold(
    tmp_path: Path,
) -> None:
    """Layer1 后仍超过固定阈值时，AUTO 才触发 Layer2 摘要。"""
    large_content = _read_large_file()

    conv = Conversation()
    for i in range(10):
        conv.add_user(f"分析第 {i} 份项目大文件")
        conv.add_assistant(f"第 {i} 份文件内容：\n{large_content}")

    estimated = estimate_tokens(0, conv.messages(), 0)
    assert estimated >= AUTO_COMPACT_TRIGGER_TOKENS

    provider = EchoSummaryProvider()
    in_ = _make_input(tmp_path, conv, provider, TriggerKind.AUTO, estimated=estimated)

    out = await manage_context(in_)

    assert provider.request_count == 1
    assert out.after_tokens < out.before_tokens
    summaries = [m for m in conv.messages() if is_compact_summary(m)]
    assert len(summaries) == 1


@pytest.mark.asyncio
async def test_auto_compact_skips_when_below_threshold(tmp_path: Path) -> None:
    """对话历史 token 低于阈值时 AUTO 不触发压缩。"""
    conv = Conversation()
    conv.add_user("简单问题")
    conv.add_assistant("简单回答")

    provider = EchoSummaryProvider()
    in_ = _make_input(tmp_path, conv, provider, TriggerKind.AUTO, estimated=500)

    await manage_context(in_)

    assert provider.request_count == 0
    msgs = conv.messages()
    assert len(msgs) == 2
    assert msgs[0].content == "简单问题"


# ── Layer 2 EMERGENCY：紧急压缩 ────────────────────────────────


@pytest.mark.asyncio
async def test_emergency_compact_with_large_history(tmp_path: Path) -> None:
    """EMERGENCY 触发忽略阈值，直接压缩。"""
    large_content = _read_large_file()

    conv = Conversation()
    for i in range(5):
        conv.add_user(f"紧急请求 {i}")
        conv.add_assistant(f"紧急回复 {i}")
        conv.add_assistant_with_tool_calls(
            "", [ToolCall(id=f"ec_{i}", name="bash", input=f'{{"command": "cat file_{i}.py"}}')]
        )
        conv.add_tool_results([ToolResult(tool_call_id=f"ec_{i}", content=large_content)])

    provider = EchoSummaryProvider("紧急压缩摘要。")
    in_ = _make_input(tmp_path, conv, provider, TriggerKind.EMERGENCY)

    await manage_context(in_)

    assert provider.request_count == 1
    msgs = conv.messages()
    summaries = [m for m in msgs if is_compact_summary(m)]
    assert len(summaries) == 1


# ── Recovery：文件快照保留 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_recovery_snapshot_preserved_after_compact(tmp_path: Path) -> None:
    """压缩后 recovery 快照中应包含之前 record_file 记录的文件内容。"""
    large_content = _read_large_file()

    conv = Conversation()
    conv.add_user("读取文件")
    conv.add_assistant("好的")

    provider = EchoSummaryProvider()
    in_ = _make_input(tmp_path, conv, provider, TriggerKind.MANUAL)

    in_.recovery.record_file("/fake/path/big_file.py", large_content)

    await manage_context(in_)

    msgs = conv.messages()
    summary_msg = [m for m in msgs if is_compact_summary(m)]
    assert len(summary_msg) == 1
    assert "最近读过的文件" in summary_msg[0].content
    assert "big_file.py" in summary_msg[0].content
    assert "上下文边界" in summary_msg[0].content


# ── 多轮压缩幂等性 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_compacts_do_not_accumulate_summaries(tmp_path: Path) -> None:
    """多次压缩后只保留一个摘要，不会堆积。"""
    large_content = _read_large_file()

    conv = Conversation()
    provider = EchoSummaryProvider()

    for round_num in range(3):
        conv.add_user(f"第 {round_num} 轮对话")
        conv.add_assistant(f"第 {round_num} 轮回复")
        conv.add_assistant_with_tool_calls(
            "", [ToolCall(id=f"r{round_num}", name="read_file", input="{}")]
        )
        conv.add_tool_results([ToolResult(tool_call_id=f"r{round_num}", content=large_content)])

        in_ = _make_input(tmp_path, conv, provider, TriggerKind.MANUAL)
        await manage_context(in_)

    msgs = conv.messages()
    summaries = [m for m in msgs if is_compact_summary(m)]
    assert len(summaries) == 1, f"期望 1 个摘要，实际有 {len(summaries)} 个"


# ── 熔断器 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_breaker_stops_auto_compact_after_failures(tmp_path: Path) -> None:
    """连续失败 3 次后，熔断器阻止后续 AUTO 压缩。"""

    class FailingProvider:
        @property
        def name(self) -> str:
            return "fail"

        @property
        def model(self) -> str:
            return "fail-model"

        async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
            yield StreamEvent(err=RuntimeError("summary failed"))

    conv = Conversation()
    conv.add_user("请求")

    breaker = CompactCircuitBreaker()
    for _ in range(3):
        breaker.record_failure()
    assert breaker.tripped()

    provider = FailingProvider()
    tool_defs = [ToolDefinition("read_file", "Read", {"type": "object"})]
    in_ = ManageInput(
        conv=conv,
        provider=provider,
        model="fail-model",
        context_window=200_000,
        tool_defs=tool_defs,
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=breaker,
        session=new_session_context(str(tmp_path)),
        usage_anchor=0,
        anchor_msg_len=0,
        estimated_token=AUTO_COMPACT_TRIGGER_TOKENS + 5000,
        trigger=TriggerKind.AUTO,
    )

    await manage_context(in_)

    assert conv.length() == 1
    assert conv.messages()[0].content == "请求"
