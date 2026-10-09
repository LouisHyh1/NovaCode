"""统一策略的入口、持久化、角色传播及费用合同。"""

import asyncio
import copy
from pathlib import Path

import pytest

from novacode.agent import Agent, CompactPhase
from novacode.agent.agent_tool import AgentTool
from novacode.agent.context_manager import CompressionDisabledError, ContextManager
from novacode.compact import TriggerKind
from novacode.compact.layer2 import COMPACT_SUMMARY_MARKER
from novacode.config import ConfigError, FeaturesConfig, ProviderConfig, load
from novacode.conversation import Conversation
from novacode.evaluation.ledger import Ledger, read_records
from novacode.evaluation.observation import ObservedProvider
from novacode.llm import Message, PromptTooLongError, Request, StreamEvent, ToolCall, ToolResult
from novacode.permission import Mode
from novacode.session import SessionService, list_sessions, load_session
from novacode.subagent import load_catalog
from novacode.task import AgentRunManager
from novacode.team.persistence import write_member_config
from novacode.tool import Registry
from novacode.tui.app import NovaCodeApp
from novacode.tui.commands import format_compact_notice
from tests.agent.test_context_manager import SummaryProvider, UnusedProvider, _runtime
from tests.evaluation.test_observation import LIMITS


def test_old_configuration_defaults_and_strict_boolean(tmp_path):
    path = tmp_path / "config.yaml"
    old = "providers:\n- name: local\n  protocol: anthropic\n  api_key: test\n  model: test\n"
    path.write_text(old)
    assert load(str(path)).features.context_compression is True
    assert FeaturesConfig().context_compression is True
    path.write_text(old + "features:\n  context_compression: false\n")
    assert load(str(path)).features.context_compression is False
    path.write_text(old + 'features:\n  context_compression: "false"\n')
    with pytest.raises(ConfigError, match="boolean"):
        load(str(path))


def large_history():
    conv = Conversation()
    conv.add_user("保留早期约束")
    conv.add_assistant_with_tool_calls("", [ToolCall("large", "read_file", "{}")])
    conv.add_tool_results([ToolResult("large", "x" * 60_000)])
    return conv


@pytest.mark.parametrize("trigger", list(TriggerKind))
async def test_disabled_shared_transaction_is_byte_preserving(tmp_path, trigger, monkeypatch):
    conv = large_history()
    before = copy.deepcopy(conv.messages())
    runtime = _runtime(tmp_path)
    runtime.usage_anchor, runtime.anchor_msg_len = 12_345, 1
    state = copy.copy(runtime)

    async def forbidden(*args, **kwargs):
        raise AssertionError("禁用入口不应执行压缩或 Hook")

    monkeypatch.setattr("novacode.agent.context_manager.offload_and_snip", forbidden)
    monkeypatch.setattr("novacode.agent.context_manager.auto_compact", forbidden)
    monkeypatch.setattr("novacode.agent.context_manager.force_compact", forbidden)
    seen = []
    manager = ContextManager(
        UnusedProvider(),
        runtime,
        context_window=200_000,
        dispatch_hook=forbidden,
        compression_enabled=False,
        observer=lambda t, r: seen.append((t, r)),
    )
    result = await manager.prepare(conv, [], trigger, Mode.DEFAULT)
    assert result.disabled and not result.offloaded and not result.summarized
    assert result.before_tokens == result.after_tokens
    assert conv.messages() == before and runtime == state
    assert manager.context_window == 200_000 and seen == [(trigger, result)]
    assert not list(Path(runtime.session.spill_dir).glob("*"))


async def test_disabled_agent_overflow_does_not_retry_or_compact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class Overflow(UnusedProvider):
        calls = 0

        async def stream(self, request):
            self.calls += 1
            yield StreamEvent(err=PromptTooLongError("实际上下文超限"))

    provider = Overflow()
    agent = Agent(provider, Registry(), context_compression=False)
    conv = large_history()
    before = copy.deepcopy(conv.messages())
    events = [e async for e in agent.run(conv, Mode.DEFAULT, asyncio.Event())]
    assert provider.calls == 1
    assert any(isinstance(e.err, PromptTooLongError) for e in events)
    assert not any(e.compact for e in events)
    assert conv.messages()[: len(before)] == before
    with pytest.raises(CompressionDisabledError, match="禁用"):
        await agent.run_force_compact(conv, [])
    assert provider.calls == 1


async def test_tui_default_assembly_and_disabled_manual_notice(tmp_path):
    cfg = ProviderConfig("local", "anthropic", "test", "test")
    app = NovaCodeApp([cfg], Registry(), project_root=tmp_path, context_compression=False)
    app.agent = app._assemble_provider_agent(cfg, UnusedProvider())
    assert app.agent.context_compression is False
    lines = []
    app.println = lines.append
    await app.force_compact()
    assert len(lines) == 1 and "禁用" in lines[0] and "已压缩" not in lines[0]
    await app.session.close()


@pytest.mark.parametrize("kind", ["raw", "summary", "offload", "transaction", "broken"])
async def test_disabled_resume_preserves_raw_or_rejects_incompatible(tmp_path, kind):
    source = SessionService.create(tmp_path, model="model")
    if kind == "offload":
        source.conversation.add_user("read")
        source.conversation.add_assistant_with_tool_calls("", [ToolCall("x", "read_file", "{}")])
        source.conversation.add_tool_results([ToolResult("x", "[tool result compacted]\nold")])
    else:
        source.conversation.add_user(
            COMPACT_SUMMARY_MARKER + "\nold" if kind == "summary" else "raw" * 60_000
        )
    if kind == "transaction":
        source.conversation.replace_history([Message(role="user", content="replacement")])
    path = source.path
    await source.close()
    if kind == "broken":
        with path.open("a") as stream:
            stream.write("invalid-json\n")
    info = next(i for i in list_sessions(path.parent) if i.path == path)
    source_bytes = path.read_bytes()
    service = SessionService.create(tmp_path, model="model")
    agent = Agent(UnusedProvider(), Registry(), context_window=33_001, context_compression=False)
    service.bind_agent(agent, lambda: [])
    old_id = service.session_id
    if kind == "raw":
        await service.resume(info)
        assert service.conversation.messages() == load_session(path).messages
        assert service.session_id == info.session_id
    else:
        with pytest.raises(ValueError, match="不兼容|不完整"):
            await service.resume(info)
        assert service.session_id == old_id and service.conversation.messages() == []
    assert path.read_bytes() == source_bytes
    assert not agent.context_compression
    await service.close()


async def test_subagent_and_pane_config_inherit_policy(tmp_path):
    parent = Agent(UnusedProvider(), Registry(), context_compression=False)
    catalog = load_catalog(tmp_path)
    tool = AgentTool(catalog, AgentRunManager(), parent=parent)
    child = tool._new_agent(catalog.fork_definition(), True)
    assert not child.context_compression
    assert child.context_window == parent.context_window
    cfg = ProviderConfig("local", "anthropic", "test", "test")
    path = tmp_path / "member.yaml"
    write_member_config(
        path, cfg, fork_teammate=True, context_compression=parent.context_compression
    )
    assert not load(str(path)).features.context_compression


async def test_skill_fork_and_hook_role_inherit_policy(tmp_path, monkeypatch):
    from novacode.agent.launch import launch_fork
    from novacode.hook.executor import Executor
    from novacode.hook.rule import SubagentAction

    parent = Agent(UnusedProvider(), Registry(), context_compression=False)
    children = []

    async def complete(child, conv, prompt, *args):
        children.append(child)
        assert not child.context_compression
        return "完成"

    monkeypatch.setattr(Agent, "run_to_completion", complete)
    assert await launch_fork(parent, Conversation()) == "完成"
    executor = Executor()
    executor.bind_subagent_runtime(parent, load_catalog(tmp_path))
    try:
        result = await executor._run_subagent(SubagentAction("Plan", "只读审查"), 2)
        assert result.output == "完成"
        assert len(children) == 2 and children[1].permission_mode == Mode.PLAN
    finally:
        await executor.close()


async def test_unaccepted_summary_still_costs_and_does_not_move_anchor(tmp_path):
    from types import SimpleNamespace

    from novacode.evaluation.budget import BudgetPolicy
    from novacode.evaluation.usage import UsageRule
    from novacode.llm.anthropic_provider import _usage_from_anthropic

    class PaidSummary(SummaryProvider):
        def request_metadata(self, request):
            return {"sdk_max_retries": 0, "timeout_seconds": 2, "max_output_tokens": 500}

        async def stream(self, request: Request):
            assert request.role == "summary"
            yield StreamEvent(
                text="摘要" * 100,
                usage=_usage_from_anthropic(SimpleNamespace(input_tokens=100, output_tokens=200)),
                done=True,
            )

    ledger = Ledger(tmp_path / "ledger.jsonl", "unaccepted")
    budget_limits = LIMITS
    budget = BudgetPolicy(total=budget_limits, categories={"test": budget_limits}).for_run(
        "test", "eager-schema"
    )
    observed = ObservedProvider(
        PaidSummary([]),
        ledger,
        budget,
        usage_rule=UsageRule("fixture", "anthropic", "subset", "a" * 64),
    )
    conv = Conversation()
    conv.add_user("短历史")
    before = conv.messages()
    runtime = _runtime(tmp_path)
    runtime.usage_anchor, runtime.anchor_msg_len = 100, 1
    hooks = []

    async def dispatch(event, mode, **values):
        from novacode.hook import DispatchResult

        hooks.append((event, values))
        return DispatchResult()

    manager = ContextManager(
        observed.borrow(), runtime, context_window=33_001, dispatch_hook=dispatch
    )
    result = await manager.prepare(conv, [], TriggerKind.AUTO, Mode.DEFAULT)
    assert result.summarized and not result.accepted
    assert conv.messages() == before and runtime.usage_anchor == 100 and runtime.anchor_msg_len == 1
    assert hooks[-1][1]["accepted"] is False
    records, _ = read_records(ledger.path)
    ends = [r["data"] for r in records if r["kind"] == "request_end"]
    assert len(ends) == 1 and ends[0]["usage"]["measured_total"] == 300
    assert "未接受" in format_compact_notice(CompactPhase.AFTER_AUTO, accepted=False)
    ledger.close()
