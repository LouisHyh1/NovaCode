"""Explicit long-term memory tool."""

import json
import logging
import uuid
from contextlib import AsyncExitStack
from typing import Any

from novacode.memory.prompts import render_memory_indexes
from novacode.memory.store import MemoryStore
from novacode.memory.types import MemoryAction, MemoryKind
from novacode.tool import Result

logger = logging.getLogger(__name__)

_USER_KINDS = frozenset({MemoryKind.USER, MemoryKind.FEEDBACK})
_FIELDS = frozenset({"action", "kind", "memory_id", "title", "summary", "content"})


class ManageMemoryTool:
    read_only = False

    def __init__(
        self,
        user_store: MemoryStore,
        project_store: MemoryStore,
        on_index_changed,
    ) -> None:
        self.user_store = user_store
        self.project_store = project_store
        self._on_index_changed = on_index_changed
        self._rejected = 0

    def name(self) -> str:
        return "manage_memory"

    def description(self) -> str:
        return (
            "Create, update, or delete durable NovaCode memory. "
            "Use this tool for explicit user requests to remember, update, or forget information."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "update", "delete"]},
                "kind": {
                    "type": "string",
                    "enum": ["user", "feedback", "project", "reference"],
                },
                "memory_id": {"type": "string", "format": "uuid"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["action", "kind"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        try:
            action = self._parse(args)
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._reject("invalid arguments")

        target = self.user_store if action.kind in _USER_KINDS else self.project_store
        try:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(self.user_store.locked())
                await stack.enter_async_context(self.project_store.locked())
                report = target.apply_locked([action])
                if report.rejected or not (report.created or report.updated or report.deleted):
                    return self._reject("store rejected action", action)
                user_index = self.user_store.render_index_locked()
                project_index = self.project_store.render_index_locked()
                self._on_index_changed(render_memory_indexes(user_index, project_index))
        except Exception as exc:
            logger.warning(
                "manage_memory failed action=%s kind=%s error=%s",
                action.action,
                action.kind.value if action.kind else "",
                type(exc).__name__,
            )
            return Result("记忆未写入：存储操作失败。", is_error=True)

        return Result(
            json.dumps(
                {
                    "ok": True,
                    "action": action.action,
                    "kind": action.kind.value,
                    "memory_id": action.memory_id,
                    "message": "记忆操作成功",
                },
                ensure_ascii=False,
            )
        )

    def _parse(self, args: str) -> MemoryAction:
        raw = json.loads(args)
        if not isinstance(raw, dict) or set(raw) - _FIELDS:
            raise ValueError("invalid fields")
        action = raw.get("action")
        if action not in {"create", "update", "delete"}:
            raise ValueError("invalid action")
        try:
            kind = MemoryKind(raw.get("kind"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid kind") from exc

        memory_id = raw.get("memory_id", "")
        title = raw.get("title", "")
        summary = raw.get("summary", "")
        content = raw.get("content", "")
        for value in (memory_id, title, summary, content):
            if not isinstance(value, str):
                raise ValueError("memory fields must be strings")

        if action == "create":
            if memory_id or not title.strip() or not summary.strip() or not content.strip():
                raise ValueError("create fields are invalid")
            memory_id = str(uuid.uuid4())
        else:
            self._validate_id(memory_id)
            if action == "update" and not any(value.strip() for value in (title, summary, content)):
                raise ValueError("update has no fields")
            if action == "delete" and any(value for value in (title, summary, content)):
                raise ValueError("delete fields are invalid")

        return MemoryAction(
            action=action,
            kind=kind,
            memory_id=memory_id,
            title=title,
            summary=summary,
            content=content,
        )

    @staticmethod
    def _validate_id(memory_id: str) -> None:
        try:
            parsed = uuid.UUID(memory_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("invalid memory ID") from exc
        if str(parsed) != memory_id.lower():
            raise ValueError("invalid memory ID")

    def _reject(self, reason: str, action: MemoryAction | None = None) -> Result:
        self._rejected += 1
        logger.warning(
            "manage_memory rejected count=%d action=%s kind=%s reason=%s",
            self._rejected,
            action.action if action else "",
            action.kind.value if action and action.kind else "",
            reason,
        )
        return Result("记忆未写入：请求无效或目标不存在。", is_error=True)
