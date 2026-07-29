import json
from collections.abc import AsyncIterator

import pytest

from novacode.agent import Agent, MaxTurnsReached, Phase
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent, ToolCall
from novacode.permission import Mode, Outcome
from novacode.permission.engine import Engine
from novacode.permission.rule import RuleSet
from novacode.tool import Registry, Result


class Provider:
    name = "fake"
    model = "fake"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.index = 0
        self.requests: list[Request] = []

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        script = self.scripts[min(self.index, len(self.scripts) - 1)]
        self.index += 1
        for event in script:
            yield event
        yield StreamEvent(done=True)


class Tool:
    def __init__(self, name: str = "echo", read_only: bool = True) -> None:
        self._name = name
        self.read_only = read_only
        self.calls = 0

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return "test"

    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, args: str) -> Result:
        self.calls += 1
        return Result(json.loads(args).get("value", "ok"))


def _engine(root: str) -> Engine:
    return Engine(root, [], RuleSet(), RuleSet(), RuleSet(), f"{root}/settings.yaml")


@pytest.mark.asyncio
async def test_run_to_completion_tools_system_prompt_and_events(tmp_path) -> None:
    provider = Provider(
        [
            [StreamEvent(tool_calls=[ToolCall("1", "echo", '{"value":"done"}')])],
            [StreamEvent(text="final")],
        ]
    )
    registry = Registry()
    tool = Tool()
    registry.register(tool)
    events = __import__("asyncio").Queue()
    agent = Agent(provider, registry, system_prompt="child system", max_turns=3)
    result = await agent.run_to_completion(Conversation(), "work", events)
    assert result == "final"
    assert tool.calls == 1
    assert provider.requests[0].system.stable == "child system"
    captured = []
    while not events.empty():
        captured.append(events.get_nowait())
    assert any(event.tool and event.tool.phase is Phase.START for event in captured)
    assert any(event.text == "final" for event in captured)


@pytest.mark.asyncio
async def test_max_turns_raises() -> None:
    call = StreamEvent(tool_calls=[ToolCall("1", "echo", "{}")])
    registry = Registry()
    registry.register(Tool())
    agent = Agent(Provider([[call], [call]]), registry, max_turns=2)
    with pytest.raises(MaxTurnsReached):
        await agent.run_to_completion(Conversation(), "work")


@pytest.mark.asyncio
@pytest.mark.parametrize("use_upgrader", [False, True])
async def test_dont_ask_and_approval_upgrader(tmp_path, use_upgrader: bool) -> None:
    provider = Provider(
        [
            [StreamEvent(tool_calls=[ToolCall("1", "side", "{}")])],
            [StreamEvent(text="ok")],
        ]
    )
    registry = Registry()
    side = Tool("side", read_only=False)
    registry.register(side)
    approvals = []

    async def upgrade(request):
        approvals.append(request)
        return Outcome.ALLOW_ONCE, True

    agent = Agent(
        provider,
        registry,
        engine=_engine(str(tmp_path)),
        permission_mode=Mode.DEFAULT,
        dont_ask=not use_upgrader,
        approval_upgrader=upgrade if use_upgrader else None,
        subagent_name="worker",
    )
    assert await agent.run_to_completion(Conversation(), "work") == "ok"
    assert side.calls == 1
    assert len(approvals) == int(use_upgrader)
    if approvals:
        assert "来自 SubAgent worker" in approvals[0].reason
