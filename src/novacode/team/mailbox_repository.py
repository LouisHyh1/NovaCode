"""按 Agent ID 寻址的事务型 JSON Mailbox Repository。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypeGuard

from novacode.adapters.json_store import (
    AtomicJsonStore,
    FaultHook,
    JsonObject,
    JsonValidator,
    JsonValue,
)
from novacode.runtime.errors import StateCorruptionError, ValidationError
from novacode.team.domain import AgentId, MailboxMessage

MAILBOX_SCHEMA_VERSION = 1
_SAFE_AGENT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class JsonMailboxRepository:
    def __init__(self, directory: str | Path, *, fault_hook: FaultHook | None = None) -> None:
        self.directory = Path(directory)
        self.fault_hook = fault_hook
        self._recovery_errors: dict[AgentId, StateCorruptionError] = {}

    async def append(self, agent_id: AgentId, message: MailboxMessage) -> None:
        store = self._store(agent_id)
        self._guard_recovery(agent_id)
        default = _empty_mailbox()

        def append_once(current: JsonObject) -> JsonObject:
            messages, revision = _decode_mailbox(current, store.path)
            if any(item.message_id == message.message_id for item in messages):
                return current
            return _encode_mailbox((*messages, message), revision + 1)

        try:
            await store.transact(default, append_once, self._validator(store.path))
        except StateCorruptionError as exc:
            self._recovery_errors[agent_id] = exc
            raise

    async def list_messages(self, agent_id: AgentId) -> tuple[MailboxMessage, ...]:
        store = self._store(agent_id)
        self._guard_recovery(agent_id)
        try:
            raw = await store.read(_empty_mailbox())
            messages, _revision = _decode_mailbox(raw, store.path)
            return messages
        except StateCorruptionError as exc:
            self._recovery_errors[agent_id] = exc
            raise

    def diagnostic(self, agent_id: AgentId) -> tuple[str, str] | None:
        error = self._recovery_errors.get(agent_id)
        if error is None:
            return None
        return error.category, error.path

    def _store(self, agent_id: AgentId) -> AtomicJsonStore:
        if not _SAFE_AGENT_ID.fullmatch(str(agent_id)):
            raise ValidationError("Agent ID 含有不安全路径字符")
        return AtomicJsonStore(
            self.directory / f"{agent_id}.json",
            fault_hook=self.fault_hook,
        )

    def _guard_recovery(self, agent_id: AgentId) -> None:
        error = self._recovery_errors.get(agent_id)
        if error is not None:
            raise error

    @staticmethod
    def _validator(path: Path) -> JsonValidator:
        def validate(raw: JsonObject) -> None:
            _decode_mailbox(raw, path)

        return validate


def _empty_mailbox() -> JsonObject:
    return {
        "schema_version": MAILBOX_SCHEMA_VERSION,
        "revision": 0,
        "messages": [],
    }


def _encode_mailbox(
    messages: tuple[MailboxMessage, ...],
    revision: int,
) -> JsonObject:
    encoded: list[JsonValue] = [
        {
            "message_id": message.message_id,
            "sender": str(message.sender),
            "text": message.text,
        }
        for message in messages
    ]
    return {
        "schema_version": MAILBOX_SCHEMA_VERSION,
        "revision": revision,
        "messages": encoded,
    }


def _decode_mailbox(
    raw: JsonObject,
    path: Path,
) -> tuple[tuple[MailboxMessage, ...], int]:
    schema_version = raw.get("schema_version")
    revision = raw.get("revision")
    messages = raw.get("messages")
    if not isinstance(schema_version, int) or schema_version != MAILBOX_SCHEMA_VERSION:
        raise StateCorruptionError(str(path), detail="mailbox schema_version 无效")
    if not isinstance(revision, int) or revision < 0:
        raise StateCorruptionError(str(path), detail="mailbox revision 无效")
    if not _is_json_list(messages):
        raise StateCorruptionError(str(path), detail="mailbox messages 无效")
    decoded = tuple(_decode_message(item, path) for item in messages)
    ids = [message.message_id for message in decoded]
    if len(set(ids)) != len(ids):
        raise StateCorruptionError(str(path), detail="mailbox message_id 重复")
    return decoded, revision


def _decode_message(value: JsonValue, path: Path) -> MailboxMessage:
    if not isinstance(value, dict):
        raise StateCorruptionError(str(path), detail="mailbox message 结构无效")
    message_id = value.get("message_id")
    sender = value.get("sender")
    text = value.get("text")
    if not isinstance(message_id, str) or not message_id:
        raise StateCorruptionError(str(path), detail="message_id 无效")
    if not isinstance(sender, str) or not sender:
        raise StateCorruptionError(str(path), detail="message sender 无效")
    if not isinstance(text, str):
        raise StateCorruptionError(str(path), detail="message text 无效")
    return MailboxMessage(message_id, AgentId(sender), text)


def _is_json_list(value: JsonValue) -> TypeGuard[list[JsonValue]]:
    return isinstance(value, list)
