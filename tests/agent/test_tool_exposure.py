"""按需 Schema 的发现、请求边界、授权与会话隔离合同。"""

import asyncio
import json

import pytest

from novacode.agent import Agent
from novacode.agent.agent_tool import AgentTool
from novacode.config import ConfigError, ProviderConfig, load
from novacode.conversation import Conversation
from novacode.evaluation.ledger import read_records
from novacode.evaluation.worker import execute, strategy
from novacode.hook import DispatchResult
from novacode.hook import Event as HookEvent
from novacode.llm import StreamEvent, ToolCall
from novacode.permission import Mode
from novacode.session import SessionService, list_sessions
from novacode.subagent import load_catalog
from novacode.task import AgentRunManager
from novacode.tool import Result, new_default_registry
from novacode.tool.exposure import BASE_TOOLS, DISCOVER, ToolExposure
from novacode.tui.app import NovaCodeApp
from tests.agent.test_context_manager import UnusedProvider
from tests.agent.test_tool_runner import DenyEngine
from tests.evaluation.test_runner import Scripted, payload


class Auxiliary:
    read_only = True

    def __init__(self, name="ci_status", description="查询 CI 构建状态", schema=None):
        self.label = name
        self.purpose = description
        self.schema = schema or {"type": "object", "properties": {"build_id": {"type": "string"}}}
        self.calls = 0

    def name(self):
        return self.label

    def description(self):
        return self.purpose

    def parameters(self):
        return self.schema

    async def execute(self, args):
        self.calls += 1
        return Result("BUILD-731 passed")


def tools():
    registry = new_default_registry()
    auxiliary = Auxiliary()
    registry.register(auxiliary)
    return registry, auxiliary


def discover(state, query):
    return json.loads(state.discover(json.dumps({"query": query})).content)


def names(agent, mode=Mode.DEFAULT):
    return {t.name for t in agent._tool_definitions(mode)}


def test_defaults_boolean_and_equal_registered_capability(tmp_path):
    path = tmp_path / "config.yaml"
    old = "providers:\n- name: local\n  protocol: anthropic\n  api_key: test\n  model: test\n"
    path.write_text(old)
    assert load(str(path)).features.progressive_tool_schema is False
    path.write_text(old + "features:\n  progressive_tool_schema: true\n")
    assert load(str(path)).features.progressive_tool_schema is True
    path.write_text(old + 'features:\n  progressive_tool_schema: "true"\n')
    with pytest.raises(ConfigError, match="boolean"):
        load(str(path))
    registry, _ = tools()
    eager = Agent(UnusedProvider(), registry)
    progressive = Agent(UnusedProvider(), registry, progressive_tool_schema=True)
    assert names(eager) == BASE_TOOLS | {DISCOVER, "ci_status"}
    assert names(progressive) == BASE_TOOLS | {DISCOVER}
    assert eager.registry is progressive.registry and registry.count() == 7


def test_deterministic_bounded_discovery_and_independent_state():
    registry, auxiliary = tools()
    for index in range(8):
        registry.register(Auxiliary(f"ci_logs_{index}", "查询 CI 构建日志"))
    state = ToolExposure(registry, progressive=True)
    state.prepare(Mode.DEFAULT, None)
    assert "input_schema" not in state.catalog() and "CI" in state.catalog()
    assert discover(state, "absent")["matched"] == 0
    result = discover(state, "CI 构建")
    assert len(result["tools"]) == 5 and result["truncated"] and result["matched"] == 9
    assert [t["name"] for t in result["tools"]] == sorted(t["name"] for t in result["tools"])
    exact = discover(state, "ＣＩ＿ＳＴＡＴＵＳ")
    assert exact["tools"][0]["input_schema"] == auxiliary.parameters()
    assert discover(state, "ci_status")["added"] == [] and auxiliary.calls == 0
    separate = ToolExposure(registry, progressive=True)
    separate.prepare(Mode.DEFAULT, None)
    assert not separate.can_execute("ci_status")
    assert state.discover("[]").is_error and state.discover('{"query": 1}').is_error
    giant = Auxiliary("giant", schema={"type": "object", "description": "x" * 40_000})
    registry.register(giant)
    state.prepare(Mode.DEFAULT, None)
    oversized = discover(state, "giant")
    assert oversized["truncated"] and not oversized["tools"] and not state.can_execute("giant")


async def batch(agent, calls, mode=Mode.DEFAULT):
    agent._tool_definitions(mode)
    updates = [
        u async for u in agent._tool_runner.run(calls, Conversation(), asyncio.Event(), mode)
    ]
    return updates[-1].result.results


async def test_same_request_guess_is_denied_and_next_request_uses_discovery():
    registry, auxiliary = tools()
    agent = Agent(UnusedProvider(), registry, progressive_tool_schema=True)
    results = await batch(
        agent,
        [
            ToolCall("discover", DISCOVER, '{"query":"ci_status"}'),
            ToolCall("guess", "ci_status", "{}"),
            ToolCall("unknown", "missing", "{}"),
        ],
    )
    assert not results[0].is_error and "尚未曝光" in results[1].content
    assert results[2].error_type == "UnknownTool" and auxiliary.calls == 0
    assert "ci_status" in names(agent)
    assert not (await batch(agent, [ToolCall("use", "ci_status", "{}")]))[0].is_error
    assert auxiliary.calls == 1


async def test_permissions_hooks_plan_and_role_cannot_be_expanded():
    registry, auxiliary = tools()
    write = Auxiliary("write_ci", "写入 CI 状态")
    write.read_only = False
    registry.register(write)
    events = []
    agent = Agent(
        UnusedProvider(),
        registry,
        progressive_tool_schema=True,
        allowed_tools=["ci_status", "write_ci"],
    )

    async def hook(event, mode, **kwargs):
        events.append(event)
        return DispatchResult(
            blocked=kwargs["tool_name"] == "ci_status",
            blocking_hook_name="fixture",
            reason="blocked",
        )

    agent._tool_runner._dispatch_hook = hook
    await batch(agent, [ToolCall("d", DISCOVER, '{"query":"ci_status"}')])
    result = (await batch(agent, [ToolCall("x", "ci_status", "{}")]))[0]
    assert result.is_error and auxiliary.calls == 0
    assert HookEvent.PRE_TOOL_USE in events and HookEvent.POST_TOOL_USE in events
    agent._tool_runner._engine = DenyEngine()
    result = (await batch(agent, [ToolCall("d2", DISCOVER, '{"query":"write_ci"}')]))[0]
    assert result.is_error and not agent.tool_exposure.can_execute("write_ci")
    agent._tool_runner._engine = None
    assert discover_after_prepare(agent, "写入", Mode.PLAN)["matched"] == 0
    denied = (await batch(agent, [ToolCall("w", "write_ci", "{}")], Mode.PLAN))[0]
    assert denied.is_policy_denial and write.calls == 0
    agent.set_allowed_tools(["read_file"])
    assert discover_after_prepare(agent, "ci_status")["matched"] == 0
    assert (await batch(agent, [ToolCall("b", "ci_status", "{}")]))[0].is_error


def discover_after_prepare(agent, query, mode=Mode.DEFAULT):
    agent._tool_definitions(mode)
    return discover(agent.tool_exposure, query)


class DiscoveryProvider(Scripted):
    async def stream(self, req):
        if req.role == "summary":
            yield StreamEvent(
                text="构建 BUILD-731 失败，TEST_TIMEOUT，允许重试；继续只读。", done=True
            )
            return
        self.seen.append(req)
        visible = {t.name for t in req.tools}
        if len(self.seen) == 1:
            assert "ci_status" not in visible and "CI" in req.system.environment
            call = ToolCall("d", DISCOVER, '{"query":"ci_status"}')
            yield StreamEvent(tool_calls=[call], done=True)
        elif len(self.seen) == 2:
            assert "ci_status" in visible
            yield StreamEvent(tool_calls=[ToolCall("use", "ci_status", "{}")], done=True)
        else:
            yield StreamEvent(text="BUILD-731 passed", done=True)


async def test_schema_refresh_multiple_runs_compaction_and_reset(tmp_path):
    registry, auxiliary = tools()
    provider = DiscoveryProvider()
    session = SessionService.create(tmp_path, model=provider.model)
    agent = Agent(provider, registry, progressive_tool_schema=True)
    session.bind_agent(agent, lambda: agent._tool_definitions(Mode.DEFAULT))
    try:
        await agent.run_to_completion(session.conversation, "查询构建")
        assert auxiliary.calls == 1
        await agent.run_to_completion(session.conversation, "继续")
        assert "ci_status" in {t.name for t in provider.seen[-1].tools}
        await agent.run_force_compact(session.conversation, agent._tool_definitions(Mode.DEFAULT))
        assert "ci_status" in names(agent)
        await session.sync()
        info = next(i for i in list_sessions(session.path.parent) if i.path == session.path)
        await session.new_session()
        assert "ci_status" not in names(agent)
        await session.resume(info)
        assert "ci_status" in names(agent)
    finally:
        await session.close()


async def test_restore_rebuilds_changed_schema_role_policy_and_bad_cache(tmp_path):
    registry, auxiliary = tools()
    source = SessionService.create(tmp_path, model="model")
    agent = Agent(UnusedProvider(), registry, progressive_tool_schema=True)
    source.bind_agent(agent, lambda: agent._tool_definitions(Mode.DEFAULT))
    discover(agent.tool_exposure, "ci_status")
    source.conversation.add_user("原始历史")
    await source.sync()
    info = next(i for i in list_sessions(source.path.parent) if i.path == source.path)
    state = agent.tool_exposure.snapshot()
    for options in (
        {"subagent_name": "child"},
        {"allowed_tools": ["read_file"]},
        {"progressive_tool_schema": False},
    ):
        other = Agent(UnusedProvider(), registry, **({"progressive_tool_schema": True} | options))
        other._tool_definitions(Mode.DEFAULT)
        assert not other.tool_exposure.restore(state)
    auxiliary.schema = {"type": "object", "required": ["new"]}
    service = SessionService.create(tmp_path, model="model")
    other = Agent(UnusedProvider(), registry, progressive_tool_schema=True)
    service.bind_agent(other, lambda: other._tool_definitions(Mode.DEFAULT))
    await service.resume(info)
    assert "ci_status" not in names(other) and other.runtime.take_reminders()
    assert service.conversation.messages()[0].content == "原始历史"
    await service.close()
    state_path = agent.tool_exposure.state_path(source.path)
    state_path.write_text("invalid-json")
    rebuilt = SessionService.create(tmp_path, model="model")
    rebuilt_agent = Agent(UnusedProvider(), registry, progressive_tool_schema=True)
    rebuilt.bind_agent(rebuilt_agent, lambda: rebuilt_agent._tool_definitions(Mode.DEFAULT))
    await rebuilt.resume(info)
    assert "ci_status" not in names(rebuilt_agent) and rebuilt_agent.runtime.take_reminders()
    await rebuilt.close()
    await source.close()


def test_auxiliary_roles_inherit_policy_without_parent_discovery(tmp_path):
    registry, _ = tools()
    parent = Agent(UnusedProvider(), registry, progressive_tool_schema=True)
    discover_after_prepare(parent, "ci_status")
    tool = AgentTool(load_catalog(tmp_path), AgentRunManager())
    tool.set_parent(parent)
    definition = tool.catalog.resolve("general-purpose")
    child = tool._new_agent(definition, False)
    assert child.progressive_tool_schema and "ci_status" not in names(child)


async def test_tui_manual_and_resume_definitions_follow_active_agent_policy(tmp_path):
    registry, _ = tools()
    config = ProviderConfig("local", "anthropic", "unused", "model")
    app = NovaCodeApp([config], registry, project_root=tmp_path, progressive_tool_schema=True)
    app.agent = app._assemble_provider_agent(config, UnusedProvider())
    assert {t.name for t in app._current_tool_defs()} == BASE_TOOLS | {DISCOVER}
    discover(app.agent.tool_exposure, "ci_status")
    assert "ci_status" in {t.name for t in app._current_tool_defs()}
    app.agent.set_allowed_tools(["read_file"])
    assert {t.name for t in app._current_tool_defs()} == {"read_file", DISCOVER}
    app._mode = Mode.PLAN
    assert {t.name for t in app._current_tool_defs()} == {"read_file", DISCOVER}
    await app.session.close()


@pytest.mark.parametrize("config", ["full", "no-compression", "eager-schema"])
async def test_evaluation_groups_equal_allowed_set_and_discovery_cost(tmp_path, config):
    registry, auxiliary = tools()
    data = payload()
    data["config_id"] = config
    data["allowed_tools"].append("ci_status")
    data["requests"] = ["查询构建"]
    data["limits"]["seconds"] = data["total_limits"]["seconds"] = 30
    root, output = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    output.mkdir()
    provider = Scripted(call=ToolCall("discover", DISCOVER, '{"query":"ci_status"}'))
    result = await execute(data, root, output, registry=registry, provider=provider)
    assert result["termination"] == "completed" and result["cleanup"] == "passed"
    assert auxiliary.calls == 0 and result["metrics"]["discovery_calls"] == 1
    records, _ = read_records(output / "ledger.jsonl")
    starts = [r["data"] for r in records if r["kind"] == "request_start"]
    assert ("ci_status" in starts[0]["visible_tools"]) == (config == "eager-schema")
    assert "ci_status" in starts[1]["visible_tools"]
    assert starts[0]["schema_sha256"] != starts[1]["schema_sha256"] or config == "eager-schema"
    assert strategy(data) == (config != "no-compression", config != "eager-schema")
    with pytest.raises(ValueError, match="Schema"):
        strategy(data | {"progressive_tool_schema": config == "eager-schema"})
