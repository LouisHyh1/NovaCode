"""Team 共享任务列表。"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from novacode.team.filelock import acquire
from novacode.team.persistence import atomic_write_json, read_json


class Status(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class Task:
    id: str = ""
    title: str = ""
    description: str = ""
    status: Status = Status.PENDING
    assignee: str = ""
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0
    is_ready: bool = True

    def to_dict(self, *, include_ready: bool = False) -> dict:
        value = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "assignee": self.assignee,
            "blocked_by": list(self.blocked_by),
            "blocks": list(self.blocks),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_ready:
            value["is_ready"] = self.is_ready
        return value

    @classmethod
    def from_dict(cls, value: dict) -> Task:
        return cls(
            id=str(value.get("id", "")),
            title=str(value.get("title", "")),
            description=str(value.get("description", "")),
            status=Status(value.get("status", Status.PENDING)),
            assignee=str(value.get("assignee", "")),
            blocked_by=[str(item) for item in value.get("blocked_by", [])],
            blocks=[str(item) for item in value.get("blocks", [])],
            created_at=int(value.get("created_at", 0)),
            updated_at=int(value.get("updated_at", 0)),
        )


@dataclass
class Filter:
    status: Status | None = None


@dataclass
class Patch:
    title: str | None = None
    description: str | None = None
    status: Status | None = None
    assignee: str | None = None
    add_blocks: list[str] = field(default_factory=list)
    add_blocked_by: list[str] = field(default_factory=list)
    remove_blocks: list[str] = field(default_factory=list)
    remove_blocked_by: list[str] = field(default_factory=list)


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _load(self) -> list[Task]:
        try:
            raw = read_json(self.path)
        except FileNotFoundError:
            return []
        values = raw.get("tasks", []) if isinstance(raw, dict) else []
        return [Task.from_dict(value) for value in values]

    def _save(self, tasks: list[Task]) -> None:
        atomic_write_json(self.path, {"tasks": [task.to_dict() for task in tasks]})

    async def create(self, task: Task) -> str:
        async with acquire(f"{self.path}.lock"):
            tasks = self._load()
            task.id = task.id or f"task_{secrets.token_hex(3)}"
            now = int(time.time())
            task.created_at = task.created_at or now
            task.updated_at = now
            tasks.append(task)
            self._save(tasks)
        return task.id

    async def get(self, task_id: str) -> Task:
        async with acquire(f"{self.path}.lock"):
            task = next((item for item in self._load() if item.id == task_id), None)
        if task is None:
            raise KeyError(f"Team 任务不存在: {task_id}")
        return task

    async def list(self, filter_: Filter | None = None) -> list[Task]:
        async with acquire(f"{self.path}.lock"):
            tasks = self._load()
        by_id = {task.id: task for task in tasks}
        for task in tasks:
            task.is_ready = all(
                blocker in by_id and by_id[blocker].status is Status.COMPLETED
                for blocker in task.blocked_by
            )
        if filter_ is not None and filter_.status is not None:
            tasks = [task for task in tasks if task.status is filter_.status]
        return tasks

    async def update(self, task_id: str, patch: Patch) -> Task:
        async with acquire(f"{self.path}.lock"):
            tasks = self._load()
            by_id = {task.id: task for task in tasks}
            task = by_id.get(task_id)
            if task is None:
                raise KeyError(f"Team 任务不存在: {task_id}")
            for field_name in ("title", "description", "status", "assignee"):
                value = getattr(patch, field_name)
                if value is not None:
                    setattr(task, field_name, value)
            self._update_edges(task, by_id, patch)
            task.updated_at = int(time.time())
            self._save(tasks)
        return task

    @staticmethod
    def _update_edges(task: Task, by_id: dict[str, Task], patch: Patch) -> None:
        for blocker_id in patch.add_blocked_by:
            blocker = by_id.get(blocker_id)
            if blocker is None:
                raise KeyError(f"Team 任务不存在: {blocker_id}")
            if blocker_id not in task.blocked_by:
                task.blocked_by.append(blocker_id)
            if task.id not in blocker.blocks:
                blocker.blocks.append(task.id)
        for blocked_id in patch.add_blocks:
            blocked = by_id.get(blocked_id)
            if blocked is None:
                raise KeyError(f"Team 任务不存在: {blocked_id}")
            if blocked_id not in task.blocks:
                task.blocks.append(blocked_id)
            if task.id not in blocked.blocked_by:
                blocked.blocked_by.append(task.id)
        for blocker_id in patch.remove_blocked_by:
            if blocker_id in task.blocked_by:
                task.blocked_by.remove(blocker_id)
            blocker = by_id.get(blocker_id)
            if blocker is not None and task.id in blocker.blocks:
                blocker.blocks.remove(task.id)
        for blocked_id in patch.remove_blocks:
            if blocked_id in task.blocks:
                task.blocks.remove(blocked_id)
            blocked = by_id.get(blocked_id)
            if blocked is not None and task.id in blocked.blocked_by:
                blocked.blocked_by.remove(task.id)
