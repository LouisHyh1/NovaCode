import asyncio
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError

import pytest

from novacode.application.session_controller import (
    PreparedSession,
    SessionController,
    SessionDependencies,
)
from novacode.runtime.reports import OperationStatus
from novacode.runtime.turn import DoneTurnEvent, TextTurnEvent, TurnEvent, TurnRequest


class FakeEngine:
    async def run(self, request: TurnRequest) -> AsyncIterator[TurnEvent]:
        yield TextTurnEvent(request.user_input)
        if not request.cancel.is_set():
            yield DoneTurnEvent()


class FakeResource:
    def __init__(self, name: str, log: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.log = log
        self.fail = fail
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1
        self.log.append(f"close:{self.name}")
        if self.fail:
            raise RuntimeError(f"{self.name} failed")


class FakePreparer:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.sessions: dict[str, PreparedSession] = {}
        self.fail_for = ""

    async def __call__(self, session_id: str) -> PreparedSession:
        self.log.append(f"prepare:{session_id}")
        if session_id == self.fail_for:
            raise RuntimeError("prepare failed")
        prepared = PreparedSession(
            session_id=session_id,
            conversation=object(),
            engine=FakeEngine(),
            writer=FakeResource(f"{session_id}:writer", self.log),
            approval=FakeResource(f"{session_id}:approval", self.log),
            cancel=asyncio.Event(),
        )
        self.sessions[session_id] = prepared
        return prepared


def test_session_dependencies_are_immutable() -> None:
    dependencies = SessionDependencies(FakePreparer())

    with pytest.raises(FrozenInstanceError):
        dependencies.prepare = FakePreparer()  # type: ignore[misc]


@pytest.mark.asyncio
async def test_switch_prepares_then_publishes_and_closes_old_resources() -> None:
    prepare = FakePreparer()
    controller = SessionController(SessionDependencies(prepare))
    await controller.start("old")

    report = await controller.switch_session("new")

    assert controller.active_session_id == "new"
    assert prepare.log == [
        "prepare:old",
        "prepare:new",
        "close:old:writer",
        "close:old:approval",
    ]
    assert report.status is OperationStatus.COMPLETED


@pytest.mark.asyncio
async def test_failed_switch_leaves_old_session_active() -> None:
    prepare = FakePreparer()
    controller = SessionController(SessionDependencies(prepare))
    await controller.start("old")
    prepare.fail_for = "broken"

    with pytest.raises(RuntimeError, match="prepare failed"):
        await controller.switch_session("broken")

    assert controller.active_session_id == "old"
    assert prepare.sessions["old"].writer.close_count == 0


@pytest.mark.asyncio
async def test_submit_and_cancel_use_active_session_state() -> None:
    prepare = FakePreparer()
    controller = SessionController(SessionDependencies(prepare))
    await controller.start("active")

    events = [event async for event in controller.submit("hello", mode=1)]
    controller.cancel()

    assert [type(event) for event in events] == [TextTurnEvent, DoneTurnEvent]
    assert prepare.sessions["active"].cancel.is_set()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_reports_every_resource_once() -> None:
    prepare = FakePreparer()
    controller = SessionController(SessionDependencies(prepare))
    await controller.start("active")
    prepared = prepare.sessions["active"]
    prepared.approval.fail = True

    first = await controller.close()
    second = await controller.close()

    assert first == second
    assert first.status is OperationStatus.INCOMPLETE
    assert prepared.writer.close_count == 1
    assert prepared.approval.close_count == 1


@pytest.mark.asyncio
async def test_concurrent_close_calls_share_one_cleanup_report() -> None:
    prepare = FakePreparer()
    controller = SessionController(SessionDependencies(prepare))
    await controller.start("active")
    prepared = prepare.sessions["active"]

    first, second = await asyncio.gather(controller.close(), controller.close())

    assert first == second
    assert prepared.writer.close_count == 1
    assert prepared.approval.close_count == 1
