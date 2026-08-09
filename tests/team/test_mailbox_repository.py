from __future__ import annotations

import asyncio

import pytest

from novacode.team.domain import AgentId, MailboxMessage
from novacode.team.mailbox_repository import JsonMailboxRepository


def _append_mailbox_message(directory: str, index: int, start, errors) -> None:
    async def run() -> None:
        repository = JsonMailboxRepository(directory)
        await asyncio.to_thread(start.wait, 5)
        await repository.append(
            AgentId("agent-a"),
            MailboxMessage(
                message_id=f"message-{index}",
                sender=AgentId(f"sender-{index}"),
                text=str(index),
            ),
        )

    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - 子进程必须把失败传回父进程
        errors.put(repr(exc))


@pytest.mark.asyncio
async def test_multiprocess_mailbox_append_is_exactly_once(isolated_state, spawn_context) -> None:
    start = spawn_context.Event()
    errors = spawn_context.Queue()
    processes = [
        spawn_context.Process(
            target=_append_mailbox_message,
            args=(str(isolated_state.mailbox), index, start, errors),
        )
        for index in range(8)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        await asyncio.to_thread(process.join, 10)

    assert [process.exitcode for process in processes] == [0] * 8
    assert errors.empty()
    repository = JsonMailboxRepository(isolated_state.mailbox)
    messages = await repository.list_messages(AgentId("agent-a"))
    assert {message.message_id for message in messages} == {
        f"message-{index}" for index in range(8)
    }
    assert len(messages) == 8


@pytest.mark.asyncio
async def test_repeated_mailbox_message_id_is_idempotent(isolated_state) -> None:
    repository = JsonMailboxRepository(isolated_state.mailbox)
    message = MailboxMessage("message-1", AgentId("sender"), "hello")

    await repository.append(AgentId("agent-a"), message)
    await repository.append(AgentId("agent-a"), message)

    assert await repository.list_messages(AgentId("agent-a")) == (message,)
