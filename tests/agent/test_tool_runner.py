import asyncio

from novacode.agent import Phase
from novacode.agent.tool_runner import ToolRunner
from novacode.conversation import Conversation
from novacode.hook import DispatchResult
from novacode.hook import Event as HookEvent
from novacode.llm import ToolCall
from novacode.permission import Decision, Mode, Outcome
from novacode.tool import Registry, Result


class ConcurrentReadTool:
    read_only = True
    active = 0
    max_active = 0
    order: list[str] = []

    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return self._name

    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, args: str) -> Result:
        type(self).order.append(f"start:{self._name}")
        type(self).active += 1
        type(self).max_active = max(type(self).max_active, type(self).active)
        await asyncio.sleep(0.01)
        type(self).active -= 1
        type(self).order.append(f"end:{self._name}")
        return Result(f"result:{self._name}")


class SideEffectTool(ConcurrentReadTool):
    read_only = False


class CountingTool(ConcurrentReadTool):
    executed = 0

    async def execute(self, args: str) -> Result:
        type(self).executed += 1
        return Result("unexpected")


class BlockingReadTool(ConcurrentReadTool):
    def __init__(self, name: str, started: asyncio.Event) -> None:
        super().__init__(name)
        self.started = started

    async def execute(self, args: str) -> Result:
        self.started.set()
        await asyncio.Event().wait()
        return Result("unreachable")


class DenyEngine:
    root = "."

    def check(self, mode: Mode, call: ToolCall, read_only: bool):
        return Decision.DENY, "policy denied"


class AskEngine(DenyEngine):
    def check(self, mode: Mode, call: ToolCall, read_only: bool):
        return Decision.ASK, "approval required"


async def _dispatch_hook(*args, **kwargs) -> DispatchResult:
    return DispatchResult()


async def test_read_only_batch_runs_concurrently_and_preserves_order() -> None:
    ConcurrentReadTool.active = 0
    ConcurrentReadTool.max_active = 0
    ConcurrentReadTool.order = []
    registry = Registry()
    registry.register(ConcurrentReadTool("first"))
    registry.register(ConcurrentReadTool("second"))
    runner = ToolRunner(registry, dispatch_hook=_dispatch_hook)
    calls = [
        ToolCall(id="1", name="first", input="{}"),
        ToolCall(id="2", name="second", input="{}"),
    ]

    updates = [
        update
        async for update in runner.run(
            calls,
            Conversation(),
            asyncio.Event(),
            Mode.DEFAULT,
        )
    ]

    final = updates[-1].result
    assert final is not None
    assert [result.content for result in final.results] == ["result:first", "result:second"]
    assert ConcurrentReadTool.max_active == 2
    tool_events = [update.event.tool for update in updates if update.event is not None]
    assert [(event.name, event.phase) for event in tool_events] == [
        ("first", Phase.START),
        ("second", Phase.START),
        ("first", Phase.END),
        ("second", Phase.END),
    ]


async def test_read_batches_are_separated_by_serial_side_effects() -> None:
    ConcurrentReadTool.active = 0
    ConcurrentReadTool.max_active = 0
    ConcurrentReadTool.order = []
    registry = Registry()
    registry.register(ConcurrentReadTool("first"))
    registry.register(ConcurrentReadTool("second"))
    registry.register(SideEffectTool("write"))
    registry.register(ConcurrentReadTool("third"))
    runner = ToolRunner(registry, dispatch_hook=_dispatch_hook)
    calls = [
        ToolCall(id="1", name="first", input="{}"),
        ToolCall(id="2", name="second", input="{}"),
        ToolCall(id="3", name="write", input="{}"),
        ToolCall(id="4", name="third", input="{}"),
    ]

    updates = [
        update async for update in runner.run(calls, Conversation(), asyncio.Event(), Mode.DEFAULT)
    ]

    assert updates[-1].result is not None
    assert ConcurrentReadTool.order.index("start:write") > ConcurrentReadTool.order.index(
        "end:second"
    )
    assert ConcurrentReadTool.order.index("start:third") > ConcurrentReadTool.order.index(
        "end:write"
    )


async def test_permission_denial_skips_execution_and_still_runs_tool_hooks() -> None:
    CountingTool.executed = 0
    hooks: list[HookEvent] = []

    async def dispatch(event: HookEvent, mode: Mode, **values) -> DispatchResult:
        hooks.append(event)
        return DispatchResult()

    registry = Registry()
    registry.register(CountingTool("read"))
    runner = ToolRunner(registry, engine=DenyEngine(), dispatch_hook=dispatch)

    updates = [
        update
        async for update in runner.run(
            [ToolCall(id="1", name="read", input="{}")],
            Conversation(),
            asyncio.Event(),
            Mode.DEFAULT,
        )
    ]

    final = updates[-1].result
    assert final is not None
    assert CountingTool.executed == 0
    assert final.results[0].content == "policy denied"
    assert final.results[0].is_error is True
    assert hooks == [HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE]


async def test_approval_event_is_exposed_before_side_effect_executes() -> None:
    ConcurrentReadTool.order = []
    registry = Registry()
    registry.register(SideEffectTool("write"))
    runner = ToolRunner(registry, engine=AskEngine(), dispatch_hook=_dispatch_hook)
    approval_seen = False
    updates = []

    async for update in runner.run(
        [ToolCall(id="1", name="write", input="{}")],
        Conversation(),
        asyncio.Event(),
        Mode.DEFAULT,
    ):
        updates.append(update)
        if update.event is not None and update.event.approval is not None:
            approval_seen = True
            assert ConcurrentReadTool.order == []
            update.event.approval.respond.set_result(Outcome.ALLOW_ONCE)

    assert approval_seen is True
    assert updates[-1].result is not None
    assert updates[-1].result.results[0].is_error is False


async def test_cancellation_stops_running_tools_and_returns_paired_results() -> None:
    started = asyncio.Event()
    cancel = asyncio.Event()
    registry = Registry()
    registry.register(BlockingReadTool("blocking", started))
    runner = ToolRunner(registry, dispatch_hook=_dispatch_hook)

    async def collect():
        return [
            update
            async for update in runner.run(
                [ToolCall(id="1", name="blocking", input="{}")],
                Conversation(),
                cancel,
                Mode.DEFAULT,
            )
        ]

    task = asyncio.create_task(collect())
    await started.wait()
    cancel.set()
    updates = await asyncio.wait_for(task, timeout=0.2)

    final = updates[-1].result
    assert final is not None
    assert final.completed is False
    assert len(final.results) == 1
    assert final.results[0].tool_call_id == "1"
    assert final.results[0].is_error is True
