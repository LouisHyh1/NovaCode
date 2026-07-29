import asyncio

import pytest

from novacode.agent import Agent
from novacode.conversation import Conversation
from novacode.llm import StreamEvent
from novacode.task import BackgroundTask, Manager, Status
from novacode.tool import Registry
from novacode.tui.app import NovaCodeApp
from novacode.tui.tasks import build_task_notification


def test_build_task_notification() -> None:
    task = BackgroundTask("task_1", "worker", object(), object(), "work")
    task.status = Status.COMPLETED
    task.result = "done"
    notification = build_task_notification(task)
    assert notification.startswith("<task-notification>")
    assert 'Task task_1 (name="worker"): completed' in notification
    assert "Result: done" in notification
    assert notification.endswith("</task-notification>")


@pytest.mark.asyncio
async def test_consume_task_done_injects_runtime_reminder() -> None:
    class Provider:
        name = "fake"
        model = "fake"

        async def stream(self, request):
            yield StreamEvent(text="done")
            yield StreamEvent(done=True)

    manager = Manager()
    agent = Agent(Provider(), Registry())
    child = Agent(Provider(), Registry())

    class Host:
        task_mgr = manager

        def __init__(self) -> None:
            self.agent = agent

    consumer = asyncio.create_task(NovaCodeApp._consume_task_done(Host()))
    task_id = await manager.launch(child, Conversation(), "worker", "work")
    for _ in range(100):
        await asyncio.sleep(0.001)
        if agent.runtime.pending_reminders:
            break
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)
    assert task_id in agent.runtime.pending_reminders[0]
    assert "Result: done" in agent.runtime.pending_reminders[0]
