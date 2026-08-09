import asyncio
from collections.abc import AsyncIterator

import pytest

from novacode.agent import Event
from novacode.application.session_controller import PreparedSession
from novacode.cli import compose_legacy_session_controller
from novacode.conversation import Conversation
from novacode.runtime.turn import DoneTurnEvent, TextTurnEvent, TurnRequest


class Resource:
    async def close(self) -> None:
        return None


class PlaceholderEngine:
    async def run(self, request: TurnRequest):
        raise AssertionError("组合根应替换占位 Turn Engine")
        yield


class LegacyAgent:
    def run(self, conversation, mode, cancel) -> AsyncIterator[Event]:
        async def events() -> AsyncIterator[Event]:
            yield Event(text="legacy")
            yield Event(done=True)

        return events()


@pytest.mark.asyncio
async def test_cli_composes_legacy_engine_into_session_controller() -> None:
    async def prepare(session_id: str) -> PreparedSession:
        return PreparedSession(
            session_id=session_id,
            conversation=Conversation(),
            engine=PlaceholderEngine(),
            writer=Resource(),
            approval=Resource(),
            cancel=asyncio.Event(),
        )

    controller = compose_legacy_session_controller(LegacyAgent(), prepare)
    await controller.start("session")

    events = [event async for event in controller.submit("hello")]

    assert [type(event) for event in events] == [TextTurnEvent, DoneTurnEvent]
