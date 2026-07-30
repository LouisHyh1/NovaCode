"""后台 SubAgent 任务生命周期管理。"""

import asyncio
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from novacode.agent import ApprovalRequest, Event, Phase
from novacode.conversation import Conversation
from novacode.permission import Outcome

if TYPE_CHECKING:
    from novacode.agent import Agent


class Status(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class PartialState:
    last_assistant_text: str = ""
    tool_count: int = 0
    last_activity: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass
class BackgroundTask:
    id: str
    name: str
    sub_agent: "Agent"
    conv: Conversation
    task: str
    status: Status = Status.RUNNING
    result: str = ""
    err: BaseException | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float = 0.0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    handle: asyncio.Task | None = None
    watcher: asyncio.Task | None = None
    usage: Usage = field(default_factory=Usage)
    tool_count: int = 0
    last_activity: str = ""
    cwd: str = ""


class TaskNotFound(LookupError):  # noqa: N818 - 对外 API 名沿用章节文档
    pass


class TaskBusy(RuntimeError):  # noqa: N818 - 对外 API 名沿用章节文档
    pass


class Manager:
    """在单个 asyncio 事件循环中管理后台任务。"""

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._by_name: dict[str, str] = {}
        self._done: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._approval_q: asyncio.Queue[ApprovalRequest] = asyncio.Queue()
        self._name_registry = None
        self._done_callbacks: list[Callable[[str], Awaitable[None]]] = []

    async def launch(
        self,
        ag: "Agent",
        conv: Conversation,
        name: str,
        task: str,
        *,
        task_id: str = "",
        cwd: str = "",
    ) -> str:
        task_id = task_id or f"task_{uuid.uuid4().hex[:8]}"
        background = BackgroundTask(task_id, name, ag, conv, task, cwd=cwd)
        self._tasks[task_id] = background
        if name:
            self._by_name[name] = task_id
            if self._name_registry is not None:
                self._name_registry.register(name, task_id)
        self._start(background, task if conv.last_role() != "user" else "")
        return task_id

    async def adopt_running(
        self,
        ag: "Agent",
        conv: Conversation,
        name: str,
        events: asyncio.Queue,
        handle: asyncio.Task,
        partial: PartialState | None = None,
        task: str = "",
    ) -> str:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        state = partial or PartialState()
        background = BackgroundTask(
            task_id,
            name,
            ag,
            conv,
            task,
            usage=state.usage,
            tool_count=state.tool_count,
            last_activity=state.last_activity,
        )
        background.handle = handle
        self._tasks[task_id] = background
        if name:
            self._by_name[name] = task_id
            if self._name_registry is not None:
                self._name_registry.register(name, task_id)
        background.watcher = asyncio.create_task(self._watch(background, events))
        return task_id

    def _start(self, background: BackgroundTask, task: str) -> None:
        from novacode.tool import with_cwd

        events: asyncio.Queue = asyncio.Queue()
        if background.cwd:
            with with_cwd(background.cwd):
                background.handle = asyncio.create_task(
                    background.sub_agent.run_to_completion(background.conv, task, events)
                )
        else:
            background.handle = asyncio.create_task(
                background.sub_agent.run_to_completion(background.conv, task, events)
            )
        background.watcher = asyncio.create_task(self._watch(background, events))

    async def _watch(self, background: BackgroundTask, events: asyncio.Queue) -> None:
        assert background.handle is not None
        aggregate = asyncio.create_task(self._aggregate(background, events))
        try:
            background.result = await background.handle
            background.status = Status.COMPLETED
        except asyncio.CancelledError:
            background.status = Status.CANCELLED
        except BaseException as exc:
            background.err = exc
            background.status = Status.FAILED
        finally:
            await events.put(None)
            await aggregate
            background.end_time = time.monotonic()
            try:
                self._done.put_nowait(background.id)
            except asyncio.QueueFull:
                print(f"task notification queue full: {background.id}", file=sys.stderr)
            for callback in self._done_callbacks:
                try:
                    await callback(background.id)
                except Exception as exc:
                    print(
                        f"task done callback failed: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )

    @staticmethod
    async def _aggregate(background: BackgroundTask, events: asyncio.Queue) -> None:
        while True:
            event: Event | None = await events.get()
            if event is None:
                return
            if event.text:
                background.last_activity = "text"
            if event.tool is not None and event.tool.phase == Phase.START:
                background.tool_count += 1
                background.last_activity = event.tool.name
            if event.usage is not None:
                background.usage.input += event.usage.input
                background.usage.output += event.usage.output
                background.usage.cache_write += event.usage.cache_write
                background.usage.cache_read += event.usage.cache_read

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[BackgroundTask]:
        return sorted(self._tasks.values(), key=lambda item: item.start_time)

    async def stop(self, task_id: str) -> bool:
        background = self.get(task_id)
        if background is None:
            return False
        if background.status is not Status.RUNNING or background.handle is None:
            return False
        background.cancel_event.set()
        background.handle.cancel()
        if background.watcher is not None:
            await background.watcher
        return True

    def subscribe_done(self) -> asyncio.Queue[str]:
        return self._done

    def subscribe_approvals(self) -> asyncio.Queue[ApprovalRequest]:
        return self._approval_q

    def set_name_registry(self, registry) -> None:
        self._name_registry = registry

    def on_task_done(self, callback: Callable[[str], Awaitable[None]]) -> None:
        self._done_callbacks.append(callback)

    async def close(self) -> None:
        """取消仍在运行的任务；会话退出后后台任务不持久化。"""
        running = [
            task
            for task in self._tasks.values()
            if task.status is Status.RUNNING and task.handle is not None
        ]
        for task in running:
            task.handle.cancel()
        await asyncio.gather(
            *(task.watcher for task in running if task.watcher is not None),
            return_exceptions=True,
        )

    async def upgrade_approval(self, request: ApprovalRequest) -> tuple[Outcome, bool]:
        await self._approval_q.put(request)
        try:
            return await request.respond, True
        except asyncio.CancelledError:
            if not request.respond.done():
                request.respond.set_result(Outcome.DENY_ONCE)
            raise

    async def send_message(self, name: str, message: str) -> str:
        task_id = (
            self._name_registry.resolve(name) if self._name_registry is not None else None
        ) or self._by_name.get(name)
        background = self.get(task_id) if task_id is not None else None
        if background is None:
            raise TaskNotFound(name)
        if background.status is Status.RUNNING:
            raise TaskBusy(name)
        background.conv.add_user(message)
        background.status = Status.RUNNING
        background.result = ""
        background.err = None
        self._start(background, "")
        return background.id
