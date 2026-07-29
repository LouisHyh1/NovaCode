import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from novacode.agent import Agent
from novacode.agent.agent_tool import AgentTool
from novacode.agent.context import ExecutionContext, bind, reset
from novacode.agent.fork import build_forked_messages
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.permission import Outcome
from novacode.permission.engine import new_engine
from novacode.subagent import load_catalog
from novacode.task import Manager
from novacode.tool import Registry, Result


class Provider:
    name = "fake"
    model = "fake"

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.index = 0

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        text = self.texts[self.index]
        self.index += 1
        yield StreamEvent(text=text)
        yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_agent_tool_schema_validation_and_inline(tmp_path) -> None:
    registry = Registry()
    manager = Manager()
    tool = AgentTool(load_catalog(tmp_path), manager)
    registry.register(tool)
    parent = Agent(Provider(["child result"]), registry)
    tool.set_parent(parent)

    assert tool.name() == "Agent"
    assert set(tool.parameters()["properties"]) == {
        "prompt",
        "description",
        "subagent_type",
        "model",
        "run_in_background",
        "name",
    }
    assert (await tool.execute("{}")).is_error
    unknown = await tool.execute(
        json.dumps({"prompt": "x", "description": "x", "subagent_type": "missing"})
    )
    assert unknown.is_error and "未知 subagent_type" in unknown.content
    result = await tool.execute(
        json.dumps({"prompt": "x", "description": "x", "subagent_type": "Explore"})
    )
    assert not result.is_error
    assert result.content == "child result"


@pytest.mark.asyncio
async def test_agent_tool_background_and_nested_guard(tmp_path) -> None:
    registry = Registry()
    manager = Manager()
    tool = AgentTool(load_catalog(tmp_path), manager)
    registry.register(tool)
    parent = Agent(Provider(["background result"]), registry)
    tool.set_parent(parent)
    result = await tool.execute(
        json.dumps(
            {
                "prompt": "x",
                "description": "x",
                "subagent_type": "general-purpose",
                "run_in_background": True,
                "name": "worker",
            }
        )
    )
    payload = json.loads(result.content)
    assert payload["status"] == "async_launched"
    assert await manager.subscribe_done().get() == payload["task_id"]
    assert manager.get(payload["task_id"]).result == "background result"

    fork_conv = Conversation.from_messages(build_forked_messages([], "nested"))
    child = Agent(parent.provider, registry, subagent_name="__fork__")
    token = bind(ExecutionContext(child, fork_conv))
    try:
        nested = await tool.execute(json.dumps({"prompt": "x", "description": "x"}))
    finally:
        reset(token)
    assert nested.is_error
    assert "Fork 子 Agent" in nested.content


@pytest.mark.asyncio
async def test_background_switch_disabled_blocks_fork(tmp_path) -> None:
    registry = Registry()
    tool = AgentTool(load_catalog(tmp_path), Manager(), bg_enabled=False)
    registry.register(tool)
    tool.set_parent(Agent(Provider(["unused"]), registry))
    result = await tool.execute(json.dumps({"prompt": "x", "description": "x"}))
    assert result.is_error
    assert "后台禁用" in result.content


@pytest.mark.asyncio
async def test_main_agent_calls_agent_tool_and_child_cannot_see_agent(tmp_path) -> None:
    class ScriptProvider:
        name = "fake"
        model = "fake"

        def __init__(self) -> None:
            self.requests = []
            self.scripts = [
                [
                    StreamEvent(
                        tool_calls=[
                            ToolCall(
                                "agent-1",
                                "Agent",
                                json.dumps(
                                    {
                                        "prompt": "inspect",
                                        "description": "inspect",
                                        "subagent_type": "Explore",
                                    }
                                ),
                            )
                        ]
                    )
                ],
                [StreamEvent(text="child final")],
                [StreamEvent(text="main final")],
            ]

        async def stream(self, request):
            self.requests.append(request)
            for event in self.scripts[len(self.requests) - 1]:
                yield event
            yield StreamEvent(done=True)

    provider = ScriptProvider()
    registry = Registry()
    manager = Manager()
    tool = AgentTool(load_catalog(tmp_path), manager)
    registry.register(tool)
    parent = Agent(provider, registry)
    tool.set_parent(parent)
    conversation = Conversation()
    assert await parent.run_to_completion(conversation, "delegate") == "main final"
    assert [item.name for item in provider.requests[0].tools] == ["Agent"]
    assert "Agent" not in [item.name for item in provider.requests[1].tools]
    tool_result = next(message for message in conversation.messages() if message.role == "tool")
    assert tool_result.tool_results[0].content == "child final"


@pytest.mark.asyncio
async def test_inline_timeout_adopts_running_task(tmp_path, monkeypatch) -> None:
    class SlowProvider:
        name = "fake"
        model = "fake"

        async def stream(self, request):
            await asyncio.sleep(0.03)
            yield StreamEvent(text="eventual result")
            yield StreamEvent(done=True)

    monkeypatch.setattr("novacode.agent.agent_tool.AUTO_BACKGROUND_SECONDS", 0.001)
    registry = Registry()
    manager = Manager()
    tool = AgentTool(load_catalog(tmp_path), manager)
    registry.register(tool)
    tool.set_parent(Agent(SlowProvider(), registry))
    result = await tool.execute(
        json.dumps({"prompt": "x", "description": "x", "subagent_type": "Explore"})
    )
    payload = json.loads(result.content)
    assert payload["status"] == "timed_out_to_background"
    assert await manager.subscribe_done().get() == payload["task_id"]
    assert manager.get(payload["task_id"]).result == "eventual result"


@pytest.mark.asyncio
async def test_cancelling_inline_agent_cancels_child_waiting_for_approval(tmp_path) -> None:
    class ApprovalProvider:
        name = "fake"
        model = "fake"

        async def stream(self, request):
            yield StreamEvent(tool_calls=[ToolCall("bash-1", "bash", '{"command":"echo hello"}')])
            yield StreamEvent(done=True)

    class BashTool:
        read_only = False

        def name(self):
            return "bash"

        def description(self):
            return "test bash"

        def parameters(self):
            return {"type": "object"}

        async def execute(self, args):
            return Result("hello")

    registry = Registry()
    manager = Manager()
    tool = AgentTool(load_catalog(tmp_path), manager)
    registry.register(tool)
    registry.register(BashTool())
    engine, error = new_engine(str(tmp_path))
    assert error is None
    tool.set_parent(Agent(ApprovalProvider(), registry, engine=engine))

    execution = asyncio.create_task(
        tool.execute(
            json.dumps({"prompt": "run bash", "description": "test", "subagent_type": "Explore"})
        )
    )
    request = await asyncio.wait_for(manager.subscribe_approvals().get(), timeout=1)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    await asyncio.sleep(0)

    try:
        assert request.respond.done()
    finally:
        if not request.respond.done():
            request.respond.set_result(Outcome.DENY_ONCE)
        await asyncio.sleep(0)
