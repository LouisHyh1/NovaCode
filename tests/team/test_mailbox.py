import asyncio
import os
import time

import pytest

from novacode.team.mailbox import Box, Message, MessageType


@pytest.mark.asyncio
async def test_mailbox_round_trip_mark_read_and_structured_message(tmp_path) -> None:
    box = Box(tmp_path / "mailbox")
    await box.write(
        "alice",
        Message(
            from_="lead",
            text="approved",
            type=MessageType.PLAN_APPROVAL_RESPONSE,
            request_id="req-1",
            approve=True,
        ),
    )
    indices, unread = await box.read_unread("alice")
    assert indices == [0]
    assert unread[0].request_id == "req-1"
    assert unread[0].approve is True
    await box.mark_read("alice", indices)
    assert (await box.read("alice"))[0].read is True


@pytest.mark.asyncio
async def test_ten_concurrent_writers_do_not_lose_messages(tmp_path) -> None:
    box = Box(tmp_path / "mailbox")
    await asyncio.gather(
        *(
            box.write("alice", Message(from_=f"sender-{index}", text=str(index)))
            for index in range(10)
        )
    )
    assert {message.text for message in await box.read("alice")} == {str(i) for i in range(10)}


@pytest.mark.asyncio
async def test_stale_lock_is_reclaimed(tmp_path) -> None:
    box = Box(tmp_path / "mailbox")
    lock = box.directory / "alice.lock"
    lock.write_text("")
    old = time.time() - 11
    os.utime(lock, (old, old))
    await box.write("alice", Message(from_="lead", text="hello"))
    assert [message.text for message in await box.read("alice")] == ["hello"]
