import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from novacode.agent import Agent
from novacode.llm import Message, StreamEvent
from novacode.session import (
    SessionService,
    SessionWriteError,
    SessionWriter,
    list_sessions,
    load_session,
)
from novacode.tool import Registry


@pytest.mark.asyncio
async def test_session_service_owns_create_record_switch_restore_and_close(tmp_path: Path) -> None:
    service = SessionService.create(tmp_path, model="model-a")
    first_id = service.session_id

    await service.record(Message(role="user", content="first"))
    assert [message.content for message in load_session(service.path).messages] == ["first"]

    await service.new_session()
    assert service.session_id != first_id
    assert service.conversation.messages() == []

    target = next(
        info for info in list_sessions(service.path.parent) if info.session_id == first_id
    )
    await service.resume(target)
    assert service.session_id == first_id
    assert [message.content for message in service.conversation.messages()] == ["first"]

    await service.close()
    with pytest.raises(SessionWriteError, match="closed"):
        await service.record(Message(role="user", content="late"))


class Provider:
    name = "fake"
    model = "model-a"

    async def stream(self, request):
        yield StreamEvent(text="summary")
        yield StreamEvent(done=True)

    async def close(self) -> None:
        return None


async def _target_session(root: Path, content: str = "restored"):
    target = SessionService.create(root, model="model-a")
    await target.record(Message(role="user", content=content))
    path = target.path
    await target.close()
    return next(info for info in list_sessions(path.parent) if info.path == path)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["read", "compact", "writer"])
async def test_resume_preparation_failure_rolls_back_complete_current_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    service = SessionService.create(tmp_path, model="model-a")
    await service.record(Message(role="user", content="current"))
    old_id = service.session_id
    old_conversation = service.conversation
    old_path = service.path
    target = await _target_session(tmp_path, "x" * 100)

    if failure == "read":
        monkeypatch.setattr(
            "novacode.session.service.load_session",
            lambda _: (_ for _ in ()).throw(OSError("read failed")),
        )
    elif failure == "compact":
        agent = Agent(Provider(), Registry(), context_window=1)
        agent.run_force_compact = AsyncMock(side_effect=RuntimeError("compact failed"))
        service.bind_agent(agent, lambda: [])
    else:
        monkeypatch.setattr(
            "novacode.session.service.SessionWriter.open_existing",
            lambda *args: (_ for _ in ()).throw(SessionWriteError("writer failed")),
        )

    with pytest.raises((OSError, RuntimeError, SessionWriteError), match="failed"):
        await service.resume(target)

    assert service.session_id == old_id
    assert service.conversation is old_conversation
    assert service.path == old_path
    assert [message.content for message in service.conversation.messages()] == ["current"]
    await service.close()


@pytest.mark.asyncio
async def test_slow_session_io_keeps_loop_responsive_and_preserves_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SessionService.create(tmp_path, model="model-a")
    entered = threading.Event()
    release = threading.Event()
    original_append = SessionWriter.append_message

    def slow_append(writer, message: Message) -> None:
        entered.set()
        assert release.wait(timeout=1)
        original_append(writer, message)

    monkeypatch.setattr(SessionWriter, "append_message", slow_append)

    service.conversation.add_user("one")
    service.conversation.add_assistant("two")
    assert await asyncio.to_thread(entered.wait, 0.5)

    heartbeat = asyncio.create_task(asyncio.sleep(0.01))
    await asyncio.wait_for(heartbeat, timeout=0.1)
    release.set()
    await service.sync()

    assert [message.content for message in load_session(service.path).messages] == ["one", "two"]
    await service.close()
