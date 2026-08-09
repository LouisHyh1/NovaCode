import asyncio
from collections.abc import AsyncIterator

import pytest

from novacode.adapters.legacy_agent_turn import LegacyAgentTurnEngine
from novacode.agent import ApprovalRequest, Event, Phase, ToolEvent, Usage
from novacode.conversation import Conversation
from novacode.permission import Mode
from novacode.runtime.turn import (
    ApprovalTurnEvent,
    CancelledTurnEvent,
    DoneTurnEvent,
    ErrorTurnEvent,
    TextTurnEvent,
    ToolTurnEvent,
    TurnRequest,
    UsageTurnEvent,
)


class FakeAgent:
    def __init__(self, events: list[Event]) -> None:
        self.events = events
        self.requests: list[tuple[Conversation, Mode, asyncio.Event]] = []

    async def run(
        self,
        conversation: Conversation,
        mode: Mode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event]:
        self.requests.append((conversation, mode, cancel))
        for event in self.events:
            yield event


async def _collect(engine: LegacyAgentTurnEngine, request: TurnRequest) -> list[object]:
    return [event async for event in engine.run(request)]


@pytest.mark.asyncio
async def test_legacy_agent_maps_all_public_event_categories() -> None:
    loop = asyncio.get_running_loop()
    approval = ApprovalRequest(
        name="bash",
        args="{}",
        reason="needs permission",
        respond=loop.create_future(),
    )
    legacy = FakeAgent(
        [
            Event(text="hello"),
            Event(tool=ToolEvent("read_file", "{}", Phase.START)),
            Event(approval=approval),
            Event(usage=Usage(input=1, output=2, cache_write=3, cache_read=4)),
            Event(done=True),
        ]
    )
    conversation = Conversation()
    cancel = asyncio.Event()

    events = await _collect(
        LegacyAgentTurnEngine(legacy),
        TurnRequest(conversation, "question", mode=int(Mode.PLAN), cancel=cancel),
    )

    assert [type(event) for event in events] == [
        TextTurnEvent,
        ToolTurnEvent,
        ApprovalTurnEvent,
        UsageTurnEvent,
        DoneTurnEvent,
    ]
    assert conversation.messages()[0].content == "question"
    assert legacy.requests == [(conversation, Mode.PLAN, cancel)]


@pytest.mark.asyncio
async def test_legacy_agent_maps_error_without_success() -> None:
    events = await _collect(
        LegacyAgentTurnEngine(FakeAgent([Event(err=ValueError("bad"))])),
        TurnRequest(Conversation(), ""),
    )

    assert len(events) == 1
    assert isinstance(events[0], ErrorTurnEvent)
    assert events[0].error_category == "ValueError"


@pytest.mark.asyncio
async def test_cancelled_request_never_maps_legacy_done_to_success() -> None:
    cancel = asyncio.Event()
    cancel.set()

    events = await _collect(
        LegacyAgentTurnEngine(FakeAgent([Event(done=True)])),
        TurnRequest(Conversation(), "", cancel=cancel),
    )

    assert [type(event) for event in events] == [CancelledTurnEvent]
