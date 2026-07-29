import asyncio
import json

import pytest

from novacode.conversation import Conversation
from novacode.task import (
    Manager,
    SendMessageTool,
    Status,
    TaskGetTool,
    TaskListTool,
    TaskStopTool,
)


class Agent:
    def __init__(self, results: list[str], *, block: bool = False) -> None:
        self.results = results
        self.block = block
        self.calls = 0

    async def run_to_completion(self, conv, task, events=None) -> str:
        if task:
            conv.add_user(task)
        self.calls += 1
        if self.block:
            await asyncio.Event().wait()
        return self.results[self.calls - 1]


@pytest.mark.asyncio
async def test_launch_send_message_and_done_notifications() -> None:
    manager = Manager()
    agent = Agent(["first", "second"])
    conv = Conversation()
    task_id = await manager.launch(agent, conv, "worker", "initial")
    assert await manager.subscribe_done().get() == task_id
    background = manager.get(task_id)
    assert background.status is Status.COMPLETED
    assert background.result == "first"

    assert await manager.send_message("worker", "continue") == task_id
    assert await manager.subscribe_done().get() == task_id
    assert background.status is Status.COMPLETED
    assert background.result == "second"
    assert conv.messages()[-1].content == "continue"


@pytest.mark.asyncio
async def test_stop_and_task_tools() -> None:
    manager = Manager()
    task_id = await manager.launch(Agent([], block=True), Conversation(), "slow", "wait")
    await asyncio.sleep(0)
    listing = await TaskListTool(manager).execute("{}")
    assert json.loads(listing.content)[0]["status"] == "running"
    detail = await TaskGetTool(manager).execute(json.dumps({"task_id": task_id}))
    assert json.loads(detail.content)["name"] == "slow"
    stopped = await TaskStopTool(manager).execute(json.dumps({"task_id": task_id}))
    assert not stopped.is_error
    assert manager.get(task_id).status is Status.CANCELLED
    assert await manager.subscribe_done().get() == task_id
    unknown = await TaskGetTool(manager).execute('{"task_id":"missing"}')
    assert unknown.is_error


@pytest.mark.asyncio
async def test_send_message_tool() -> None:
    manager = Manager()
    task_id = await manager.launch(Agent(["one", "two"]), Conversation(), "worker", "one")
    await manager.subscribe_done().get()
    result = await SendMessageTool(manager).execute(
        json.dumps({"name": "worker", "message": "two"})
    )
    assert json.loads(result.content) == {"task_id": task_id, "status": "resumed"}
    await manager.subscribe_done().get()
