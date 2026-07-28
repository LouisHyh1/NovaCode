"""Tests for the serialized long-term-memory extraction worker."""

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from novacode.llm import Request, StreamEvent
from novacode.memory import MemoryExtractor, MemoryKind, MemoryStore, MemoryTurn
from novacode.memory.prompts import build_extraction_prompt


class ScriptedProvider:
    def __init__(self, replies: list[str | Exception], gate: asyncio.Event | None = None) -> None:
        self.replies = replies
        self.gate = gate
        self.requests: list[Request] = []
        self.active = 0
        self.max_active = 0
        self.started = asyncio.Event()

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-memory"

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        self.started.set()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if len(self.requests) == 1 and self.gate is not None:
                await self.gate.wait()
            reply = self.replies[len(self.requests) - 1]
            if isinstance(reply, Exception):
                raise reply
            yield StreamEvent(text=reply)
            yield StreamEvent(done=True)
        finally:
            self.active -= 1


def stores(tmp_path):
    user = MemoryStore(tmp_path / "home-memory", frozenset({MemoryKind.USER, MemoryKind.FEEDBACK}))
    project = MemoryStore(
        tmp_path / "project-memory",
        frozenset({MemoryKind.PROJECT, MemoryKind.REFERENCE}),
    )
    return user, project


def create(kind: str, title: str) -> str:
    return json.dumps(
        [
            {
                "action": "create",
                "kind": kind,
                "title": title,
                "summary": f"{title} summary",
                "content": f"{title} body",
            }
        ]
    )


def noop() -> str:
    return '[{"action":"no-op"}]'


def test_extraction_prompt_contains_complete_contract_and_explicit_request_rule() -> None:
    prompt = build_extraction_prompt(MemoryTurn("记住我使用 Python", "已记住"), "", "")

    for field in ("action", "kind", "memory_id", "title", "summary", "content"):
        assert field in prompt
    for kind in ("user", "feedback", "project", "reference"):
        assert kind in prompt
    assert "create" in prompt and "update" in prompt and "delete" in prompt
    assert "empty array" in prompt.lower()
    assert "JSON" in prompt and "code fence" in prompt


def test_constructor_does_not_bind_or_start_and_submit_requires_binding(tmp_path) -> None:
    user, project = stores(tmp_path)
    extractor = MemoryExtractor(user, project, lambda _: None)

    assert extractor.provider is None
    assert extractor.running is False
    with pytest.raises(RuntimeError, match="provider"):
        extractor.submit(MemoryTurn("u", "a"))


def test_provider_binding_is_one_time(tmp_path) -> None:
    user, project = stores(tmp_path)
    extractor = MemoryExtractor(user, project, lambda _: None)
    first = ScriptedProvider([noop()])

    extractor.bind_provider(first)
    extractor.bind_provider(first)
    with pytest.raises(RuntimeError, match="different provider"):
        extractor.bind_provider(ScriptedProvider([noop()]))


@pytest.mark.asyncio
async def test_queue_is_nonblocking_serial_and_each_item_reads_latest_indexes(tmp_path) -> None:
    user, project = stores(tmp_path)
    gate = asyncio.Event()
    provider = ScriptedProvider(
        [create("user", "First"), create("project", "Second"), noop()], gate
    )
    snapshots: list[str] = []
    extractor = MemoryExtractor(user, project, snapshots.append)
    extractor.bind_provider(provider)
    task = asyncio.create_task(extractor.run())

    extractor.submit(MemoryTurn("one", "answer one"))
    extractor.submit(MemoryTurn("two", "answer two"))
    extractor.submit(MemoryTurn("three", "answer three"))
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    assert len(provider.requests) == 1
    assert extractor.pending == 3
    gate.set()
    await extractor.close()
    await task

    assert provider.max_active == 1
    assert len(provider.requests) == 3
    assert all(request.tools == [] for request in provider.requests)
    assert "First" in provider.requests[1].messages[0].content
    assert "Second" in provider.requests[2].messages[0].content
    assert "Latest user message:\ntwo" in provider.requests[1].messages[0].content
    assert snapshots and "First" in snapshots[-1] and "Second" in snapshots[-1]


@pytest.mark.asyncio
async def test_provider_parse_and_illegal_action_failures_do_not_stop_queue(tmp_path) -> None:
    user, project = stores(tmp_path)
    provider = ScriptedProvider(
        [RuntimeError("provider down"), "not-json", create("user", "Recovered")]
    )
    snapshots: list[str] = []
    extractor = MemoryExtractor(user, project, snapshots.append)
    extractor.bind_provider(provider)
    task = asyncio.create_task(extractor.run())

    for number in range(3):
        extractor.submit(MemoryTurn(str(number), f"answer {number}"))
    await extractor.close()
    await task

    assert len(provider.requests) == 3
    assert "Recovered" in snapshots[-1]


@pytest.mark.asyncio
async def test_illegal_route_is_rejected_and_next_item_continues(tmp_path) -> None:
    user, project = stores(tmp_path)
    provider = ScriptedProvider(
        [create("reference", "Wrong route candidate"), create("feedback", "Preference")]
    )
    snapshots: list[str] = []
    extractor = MemoryExtractor(user, project, snapshots.append)
    extractor.bind_provider(provider)
    task = asyncio.create_task(extractor.run())

    extractor.submit(MemoryTurn("one", "a"))
    extractor.submit(MemoryTurn("two", "b"))
    await extractor.close()
    await task

    assert not list(user.directory.glob("*Wrong*"))
    assert "Preference" in snapshots[-1]
    assert "Wrong route candidate" in snapshots[-1]
    assert "reference" in (project.directory / "MEMORY.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_tool_calls_are_rejected_but_worker_continues(tmp_path) -> None:
    from novacode.llm import ToolCall

    class ToolCallingProvider(ScriptedProvider):
        async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
            self.requests.append(request)
            if len(self.requests) == 1:
                yield StreamEvent(tool_calls=[ToolCall("x", "bash", "{}")])
            else:
                yield StreamEvent(text=create("project", "Safe"))
            yield StreamEvent(done=True)

    user, project = stores(tmp_path)
    provider = ToolCallingProvider([noop(), noop()])
    extractor = MemoryExtractor(user, project, lambda _: None)
    extractor.bind_provider(provider)
    task = asyncio.create_task(extractor.run())
    extractor.submit(MemoryTurn("one", "a"))
    extractor.submit(MemoryTurn("two", "b"))

    await extractor.close()
    await task

    assert "Safe" in (project.directory / "MEMORY.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_close_stops_accepting_and_drains_without_provider_reference(tmp_path) -> None:
    user, project = stores(tmp_path)
    provider = ScriptedProvider([noop()])
    extractor = MemoryExtractor(user, project, lambda _: None)
    extractor.bind_provider(provider)
    task = asyncio.create_task(extractor.run())
    extractor.submit(MemoryTurn("one", "a"))

    await extractor.close()
    await task

    assert extractor.running is False
    assert extractor.provider is None
    with pytest.raises(RuntimeError, match="closed"):
        extractor.submit(MemoryTurn("two", "b"))
