import asyncio
from collections.abc import AsyncIterator

from novacode.runtime.turn import (
    CancelledTurnEvent,
    DoneTurnEvent,
    TextTurnEvent,
    TurnEngine,
    TurnEvent,
    TurnRequest,
)


class ExampleEngine:
    async def run(self, request: TurnRequest) -> AsyncIterator[TurnEvent]:
        if request.cancel.is_set():
            yield CancelledTurnEvent()
            return
        yield TextTurnEvent(request.user_input)
        yield DoneTurnEvent()


def test_turn_engine_protocol_accepts_typed_async_stream() -> None:
    engine: TurnEngine = ExampleEngine()

    assert isinstance(engine, ExampleEngine)


def test_turn_request_owns_explicit_cancel_handle() -> None:
    cancel = asyncio.Event()

    request = TurnRequest(object(), "hello", mode=2, cancel=cancel)

    assert request.cancel is cancel
    assert request.mode == 2
