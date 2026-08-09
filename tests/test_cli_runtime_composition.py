import asyncio
import inspect

import pytest

from novacode.cli import _amain
from novacode.conversation import Conversation
from novacode.permission import Mode
from novacode.tool import Registry
from novacode.tui.app import NovaCodeApp


class DirectAgent:
    def __init__(self) -> None:
        self.calls = []

    def run(self, conversation, mode, cancel):
        self.calls.append((conversation, mode, cancel))

        async def events():
            if False:
                yield None

        return events()


class ChatArea:
    async def mount(self, widget) -> None:
        return None


@pytest.mark.asyncio
async def test_cli_starts_tui_and_tui_directly_drives_agent() -> None:
    cli_source = inspect.getsource(_amain)
    assert "NovaCodeApp(" in cli_source
    assert "SessionController" not in cli_source

    app = NovaCodeApp([], Registry(), version="test")
    agent = DirectAgent()
    app.agent = agent
    app.conv = Conversation()
    app.query_one = lambda *args, **kwargs: ChatArea()
    app._scroll_chat = lambda: None
    app._start_spinner = lambda: None

    consumed = asyncio.Event()

    async def consume(events) -> None:
        async for _ in events:
            pass
        consumed.set()

    app._consume_events = consume
    await app._start_stream()
    await app._agent_task

    assert consumed.is_set()
    assert agent.calls == [(app.conv, Mode.DEFAULT, app.turn_cancel)]
