"""Team 共享任务列表的兼容门面。"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from novacode.runtime.errors import ValidationError
from novacode.team.domain import AgentId, TeamId, TeamTask, TeamTaskGraph
from novacode.team.task_repository import JsonTeamTaskRepository


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
    """旧 API 转发到单文件 Team Task Graph Repository。"""

    def __init__(self, path: str | Path, *, team_id: str = "") -> None:
        self.path = Path(path)
        stable = uuid.uuid5(uuid.NAMESPACE_URL, f"novacode-task-graph:{self.path.resolve()}")
        self.team_id = TeamId(team_id or f"team-{stable.hex}")
        self.repository = JsonTeamTaskRepository(self.path)

    async def create(self, task: Task) -> str:
        graph = await self._load_or_create()
        task.id = task.id or f"task_{secrets.token_hex(3)}"
        now = int(time.time())
        task.created_at = task.created_at or now
        task.updated_at = now
        candidate = _to_domain(task)

        def add(current: TeamTaskGraph) -> TeamTaskGraph:
            by_id = {item.task_id: item for item in current.tasks}
            if candidate.task_id in by_id:
                raise ValidationError(f"Team Task 已存在: {candidate.task_id}")
            missing = candidate.blocked_by - by_id.keys()
            if missing:
                raise ValidationError(f"Team Task blocker 不存在: {', '.join(sorted(missing))}")
            updated = [
                replace(item, blocks=item.blocks | {candidate.task_id})
                if item.task_id in candidate.blocked_by
                else item
                for item in current.tasks
            ]
            return replace(current, tasks=(*updated, candidate))

        await self.repository.transact(graph.team_id, add)
        return task.id

    async def get(self, task_id: str) -> Task:
        graph = await self._load_or_create()
        task = next((item for item in graph.tasks if item.task_id == task_id), None)
        if task is None:
            raise KeyError(f"Team 任务不存在: {task_id}")
        return _from_domain(task)

    async def list(self, filter_: Filter | None = None) -> list[Task]:
        graph = await self._load_or_create()
        tasks = [_from_domain(task) for task in graph.tasks]
        if filter_ is not None and filter_.status is not None:
            tasks = [task for task in tasks if task.status is filter_.status]
        return tasks

    async def update(self, task_id: str, patch: Patch) -> Task:
        graph = await self._load_or_create()

        def apply(current: TeamTaskGraph) -> TeamTaskGraph:
            by_id = {item.task_id: item for item in current.tasks}
            if task_id not in by_id:
                raise KeyError(f"Team 任务不存在: {task_id}")
            _apply_fields(by_id, task_id, patch)
            _apply_edges(by_id, task_id, patch)
            by_id[task_id] = replace(by_id[task_id], updated_at=int(time.time()))
            ordered = tuple(by_id[item.task_id] for item in current.tasks)
            return replace(current, tasks=ordered)

        updated = await self.repository.transact(graph.team_id, apply)
        task = next(item for item in updated.tasks if item.task_id == task_id)
        return _from_domain(task)

    async def _load_or_create(self) -> TeamTaskGraph:
        try:
            return await self.repository.load(self.team_id)
        except KeyError:
            return await self.repository.create(TeamTaskGraph(self.team_id))


def _to_domain(task: Task) -> TeamTask:
    return TeamTask(
        task_id=task.id,
        title=task.title,
        description=task.description,
        status=task.status.value,
        assignee=AgentId(task.assignee) if task.assignee else None,
        blocked_by=frozenset(task.blocked_by),
        blocks=frozenset(task.blocks),
        created_at=task.created_at,
        updated_at=task.updated_at,
        is_ready=task.is_ready,
    )


def _from_domain(task: TeamTask) -> Task:
    return Task(
        id=task.task_id,
        title=task.title,
        description=task.description,
        status=Status(task.status),
        assignee=str(task.assignee or ""),
        blocked_by=sorted(task.blocked_by),
        blocks=sorted(task.blocks),
        created_at=task.created_at,
        updated_at=task.updated_at,
        is_ready=task.is_ready,
    )


def _apply_fields(by_id: dict[str, TeamTask], task_id: str, patch: Patch) -> None:
    task = by_id[task_id]
    values = {
        "title": patch.title if patch.title is not None else task.title,
        "description": (patch.description if patch.description is not None else task.description),
        "status": patch.status.value if patch.status is not None else task.status,
        "assignee": (
            AgentId(patch.assignee)
            if patch.assignee is not None and patch.assignee
            else (None if patch.assignee == "" else task.assignee)
        ),
    }
    by_id[task_id] = replace(task, **values)


def _apply_edges(by_id: dict[str, TeamTask], task_id: str, patch: Patch) -> None:
    for related in (
        patch.add_blocks + patch.add_blocked_by + patch.remove_blocks + patch.remove_blocked_by
    ):
        if related not in by_id:
            raise ValidationError(f"Team Task 不存在: {related}")
    task = by_id[task_id]
    for blocked_id in patch.add_blocks:
        task = replace(task, blocks=task.blocks | {blocked_id})
        blocked = by_id[blocked_id]
        by_id[blocked_id] = replace(blocked, blocked_by=blocked.blocked_by | {task_id})
    for blocker_id in patch.add_blocked_by:
        task = replace(task, blocked_by=task.blocked_by | {blocker_id})
        blocker = by_id[blocker_id]
        by_id[blocker_id] = replace(blocker, blocks=blocker.blocks | {task_id})
    for blocked_id in patch.remove_blocks:
        task = replace(task, blocks=task.blocks - {blocked_id})
        blocked = by_id[blocked_id]
        by_id[blocked_id] = replace(blocked, blocked_by=blocked.blocked_by - {task_id})
    for blocker_id in patch.remove_blocked_by:
        task = replace(task, blocked_by=task.blocked_by - {blocker_id})
        blocker = by_id[blocker_id]
        by_id[blocker_id] = replace(blocker, blocks=blocker.blocks - {task_id})
    by_id[task_id] = task
