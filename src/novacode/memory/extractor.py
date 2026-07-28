"""Serialized automatic extraction of durable memories."""

import asyncio
import logging
from collections.abc import Callable

from novacode.llm import Message, Provider, Request
from novacode.memory.prompts import (
    build_extraction_prompt,
    parse_actions,
    render_memory_indexes,
)
from novacode.memory.store import MemoryStore
from novacode.memory.types import MemoryAction, MemoryKind, MemoryTurn

logger = logging.getLogger(__name__)

_USER_KINDS = frozenset({MemoryKind.USER, MemoryKind.FEEDBACK})
_PROJECT_KINDS = frozenset({MemoryKind.PROJECT, MemoryKind.REFERENCE})


class MemoryExtractor:
    """A one-provider, one-consumer memory extraction queue."""

    def __init__(
        self,
        user_store: MemoryStore,
        project_store: MemoryStore,
        on_index_changed: Callable[[str], None],
    ) -> None:
        self.user_store = user_store
        self.project_store = project_store
        self._on_index_changed = on_index_changed
        self._queue: asyncio.Queue[MemoryTurn | None] = asyncio.Queue()
        self._provider: Provider | None = None
        self._accepting = True
        self._running = False
        self._processing = False

    @property
    def provider(self) -> Provider | None:
        return self._provider

    @property
    def running(self) -> bool:
        return self._running

    @property
    def pending(self) -> int:
        return self._queue.qsize() + int(self._processing)

    def bind_provider(self, provider: Provider) -> None:
        if provider is None:
            raise ValueError("memory extractor provider is required")
        if self._provider is provider:
            return
        if self._provider is not None:
            raise RuntimeError("memory extractor cannot bind a different provider")
        if not self._accepting:
            raise RuntimeError("memory extractor is closed")
        self._provider = provider

    def submit(self, turn: MemoryTurn) -> None:
        if not self._accepting:
            raise RuntimeError("memory extractor is closed")
        if self._provider is None:
            raise RuntimeError("memory extractor provider is not bound")
        self._queue.put_nowait(turn)

    async def run(self) -> None:
        if self._provider is None:
            raise RuntimeError("memory extractor provider is not bound")
        if self._running:
            raise RuntimeError("memory extractor consumer is already running")
        self._running = True
        try:
            while True:
                turn = await self._queue.get()
                try:
                    if turn is None:
                        return
                    self._processing = True
                    await self._process(turn)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # one failed item must not stop later items
                    logger.warning("memory extraction item failed: %s", type(exc).__name__)
                finally:
                    self._processing = False
                    self._queue.task_done()
        finally:
            self._running = False
            self._provider = None

    async def close(self) -> None:
        if not self._accepting:
            return
        self._accepting = False
        await asyncio.sleep(0)
        if not self._running:
            while True:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                else:
                    self._queue.task_done()
            self._provider = None
            return
        await self._queue.join()
        self._queue.put_nowait(None)

    async def _process(self, turn: MemoryTurn) -> None:
        provider = self._provider
        if provider is None:
            raise RuntimeError("memory extractor provider is not bound")

        async with self.user_store.locked():
            async with self.project_store.locked():
                user_index = self.user_store.read_index_locked()
                project_index = self.project_store.read_index_locked()
                request = Request(
                    messages=[
                        Message(
                            role="user",
                            content=build_extraction_prompt(turn, user_index, project_index),
                        )
                    ],
                    tools=[],
                )
                response: list[str] = []
                async for event in provider.stream(request):
                    if event.err is not None:
                        raise event.err
                    if event.tool_calls:
                        raise ValueError("memory extraction provider requested tools")
                    if event.text:
                        response.append(event.text)
                actions = parse_actions("".join(response))
                user_actions, project_actions = _route_actions(actions)
                self.user_store.apply_locked(user_actions)
                self.project_store.apply_locked(project_actions)
                latest_user = self.user_store.render_index_locked()
                latest_project = self.project_store.render_index_locked()
                self._on_index_changed(render_memory_indexes(latest_user, latest_project))


def _route_actions(
    actions: list[MemoryAction],
) -> tuple[list[MemoryAction], list[MemoryAction]]:
    user: list[MemoryAction] = []
    project: list[MemoryAction] = []
    for action in actions:
        if action.action == "no-op":
            continue
        if action.kind in _USER_KINDS:
            user.append(action)
        elif action.kind in _PROJECT_KINDS:
            project.append(action)
        else:
            raise ValueError("memory action has no valid routed kind")
    return user, project
