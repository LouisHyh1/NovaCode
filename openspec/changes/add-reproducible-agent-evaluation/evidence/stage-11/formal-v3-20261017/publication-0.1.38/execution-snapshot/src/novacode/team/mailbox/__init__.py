"""Team 文件邮箱。"""

from pathlib import Path

from novacode.team.filelock import acquire
from novacode.team.mailbox.message import Message, MessageType
from novacode.team.persistence import atomic_write_json, read_json

__all__ = ["Box", "Message", "MessageType"]


class Box:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, agent_id: str) -> Path:
        return self.directory / f"{agent_id}.json"

    def _lock_path(self, agent_id: str) -> Path:
        return self.directory / f"{agent_id}.lock"

    async def write(self, agent_id: str, message: Message) -> None:
        async with acquire(self._lock_path(agent_id)):
            path = self._path(agent_id)
            try:
                values = read_json(path)
            except FileNotFoundError:
                values = []
            if not isinstance(values, list):
                raise ValueError(f"邮箱文件格式错误: {path}")
            values.append(message.to_dict())
            atomic_write_json(path, values)

    async def read(self, agent_id: str) -> list[Message]:
        async with acquire(self._lock_path(agent_id)):
            try:
                values = read_json(self._path(agent_id))
            except FileNotFoundError:
                return []
        if not isinstance(values, list):
            raise ValueError(f"邮箱文件格式错误: {self._path(agent_id)}")
        return [Message.from_dict(value) for value in values]

    async def read_unread(self, agent_id: str) -> tuple[list[int], list[Message]]:
        messages = await self.read(agent_id)
        indices = [index for index, message in enumerate(messages) if not message.read]
        return indices, [messages[index] for index in indices]

    async def mark_read(self, agent_id: str, indices: list[int]) -> None:
        if not indices:
            return
        async with acquire(self._lock_path(agent_id)):
            path = self._path(agent_id)
            try:
                values = read_json(path)
            except FileNotFoundError:
                return
            for index in indices:
                if 0 <= index < len(values):
                    values[index]["read"] = True
            atomic_write_json(path, values)
