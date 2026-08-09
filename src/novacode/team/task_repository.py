"""Team Task Graph 的 versioned JSON Repository。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TypeGuard, cast

from novacode.adapters.json_store import AtomicJsonStore, FaultHook, JsonObject, JsonValue
from novacode.adapters.migration import write_migration_backup
from novacode.runtime.errors import (
    ConflictError,
    DependencyCycleError,
    StateCorruptionError,
    UnsupportedSchemaError,
    ValidationError,
)
from novacode.team.domain import AgentId, TeamId, TeamTask, TeamTaskGraph
from novacode.team.ports import TeamTaskMutation

TEAM_TASK_SCHEMA_VERSION = 1


class JsonTeamTaskRepository:
    """把完整 Team Task Graph 作为单个事务候选发布。"""

    def __init__(self, path: str | Path, *, fault_hook: FaultHook | None = None) -> None:
        self.path = Path(path)
        self.store = AtomicJsonStore(self.path, fault_hook=fault_hook)
        self._recovery_error: StateCorruptionError | None = None

    async def load(self, aggregate_id: TeamId) -> TeamTaskGraph:
        self._guard_recovery()
        try:
            raw = await self.store.update_if(
                {},
                lambda current: self._migration_candidate(current, aggregate_id),
            )
            if not raw:
                raise KeyError(f"Team Task Graph 不存在: {aggregate_id}")
            graph = _decode_graph(raw, self.path)
            _ensure_team_id(graph, aggregate_id)
            return _derive_readiness(graph)
        except StateCorruptionError as exc:
            self._recovery_error = exc
            raise

    async def create(self, state: TeamTaskGraph) -> TeamTaskGraph:
        self._guard_recovery()
        _validate_graph(state)
        created = replace(
            state,
            schema_version=TEAM_TASK_SCHEMA_VERSION,
            revision=1,
        )

        def create_if_empty(current: JsonObject) -> JsonObject:
            if current:
                raise ConflictError(f"Team Task Graph 已存在: {state.team_id}")
            return _encode_graph(created)

        try:
            raw = await self.store.transact({}, create_if_empty, self._validate_raw)
            return _derive_readiness(_decode_graph(raw, self.path))
        except StateCorruptionError as exc:
            self._recovery_error = exc
            raise

    async def transact(
        self,
        aggregate_id: TeamId,
        mutation: TeamTaskMutation,
    ) -> TeamTaskGraph:
        self._guard_recovery()

        def update(current: JsonObject) -> JsonObject:
            if not current:
                raise KeyError(f"Team Task Graph 不存在: {aggregate_id}")
            graph = _decode_graph(current, self.path)
            _ensure_team_id(graph, aggregate_id)
            candidate = mutation(graph)
            _ensure_team_id(candidate, aggregate_id)
            candidate = replace(
                candidate,
                schema_version=TEAM_TASK_SCHEMA_VERSION,
                revision=graph.revision + 1,
            )
            _validate_graph(candidate)
            return _encode_graph(candidate)

        try:
            raw = await self.store.transact({}, update, self._validate_raw)
            return _derive_readiness(_decode_graph(raw, self.path))
        except StateCorruptionError as exc:
            self._recovery_error = exc
            raise

    def diagnostic(self) -> tuple[str, str] | None:
        if self._recovery_error is None:
            return None
        return self._recovery_error.category, self._recovery_error.path

    def _guard_recovery(self) -> None:
        if self._recovery_error is not None:
            raise self._recovery_error

    def _validate_raw(self, raw: JsonObject) -> None:
        _decode_graph(raw, self.path)

    def _migration_candidate(
        self,
        current: JsonObject,
        aggregate_id: TeamId,
    ) -> JsonObject | None:
        if not current:
            return None
        schema_version = current.get("schema_version")
        if schema_version is None:
            graph = _decode_legacy_graph(current, aggregate_id, self.path)
            write_migration_backup(self.path)
            return _encode_graph(graph)
        if isinstance(schema_version, int) and schema_version > TEAM_TASK_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                str(self.path),
                detail=f"未知 schema_version: {schema_version}",
            )
        _decode_graph(current, self.path)
        return None


def _ensure_team_id(graph: TeamTaskGraph, expected: TeamId) -> None:
    if graph.team_id != expected:
        raise ConflictError(f"Team ID 不匹配: {expected}")


def _encode_graph(graph: TeamTaskGraph) -> JsonObject:
    tasks: list[JsonValue] = [
        {
            "id": task.task_id,
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "assignee": str(task.assignee) if task.assignee is not None else "",
            "historical_assignee": task.historical_assignee,
            "blocked_by": cast(list[JsonValue], sorted(task.blocked_by)),
            "blocks": cast(list[JsonValue], sorted(task.blocks)),
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }
        for task in graph.tasks
    ]
    return {
        "schema_version": graph.schema_version,
        "revision": graph.revision,
        "team_id": str(graph.team_id),
        "tasks": tasks,
    }


def _decode_graph(raw: JsonObject, path: Path) -> TeamTaskGraph:
    schema_version = raw.get("schema_version")
    revision = raw.get("revision")
    team_id = raw.get("team_id")
    tasks = raw.get("tasks")
    if not isinstance(schema_version, int) or schema_version != TEAM_TASK_SCHEMA_VERSION:
        raise StateCorruptionError(str(path), detail="schema_version 无效")
    if not isinstance(revision, int) or revision < 1:
        raise StateCorruptionError(str(path), detail="revision 无效")
    if not isinstance(team_id, str) or not team_id:
        raise StateCorruptionError(str(path), detail="team_id 无效")
    if not isinstance(tasks, list):
        raise StateCorruptionError(str(path), detail="tasks 无效")
    graph = TeamTaskGraph(
        TeamId(team_id),
        tuple(_decode_task(item, path) for item in tasks),
        schema_version=schema_version,
        revision=revision,
    )
    try:
        _validate_graph(graph)
    except ValidationError as exc:
        raise StateCorruptionError(str(path), detail=str(exc)) from exc
    return graph


def _decode_task(value: JsonValue, path: Path) -> TeamTask:
    if not isinstance(value, dict):
        raise StateCorruptionError(str(path), detail="task 结构无效")
    task_id = value.get("id")
    title = value.get("title")
    description = value.get("description", "")
    status = value.get("status", "pending")
    assignee = value.get("assignee", "")
    historical = value.get("historical_assignee", "")
    blocked_by = value.get("blocked_by", [])
    blocks = value.get("blocks", [])
    created_at = value.get("created_at", 0)
    updated_at = value.get("updated_at", 0)
    if not isinstance(task_id, str) or not isinstance(title, str):
        raise StateCorruptionError(str(path), detail="task 标识字段无效")
    if not isinstance(description, str) or not isinstance(status, str):
        raise StateCorruptionError(str(path), detail="task 内容字段无效")
    if not isinstance(assignee, str) or not isinstance(historical, str):
        raise StateCorruptionError(str(path), detail="task 字符串字段无效")
    if not _is_string_list(blocked_by) or not _is_string_list(blocks):
        raise StateCorruptionError(str(path), detail="task 依赖字段无效")
    if not isinstance(created_at, int) or not isinstance(updated_at, int):
        raise StateCorruptionError(str(path), detail="task 时间字段无效")
    return TeamTask(
        task_id=task_id,
        title=title,
        description=description,
        status=status,
        assignee=AgentId(assignee) if assignee else None,
        historical_assignee=historical,
        blocked_by=frozenset(blocked_by),
        blocks=frozenset(blocks),
        created_at=created_at,
        updated_at=updated_at,
    )


def _is_string_list(value: JsonValue) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_graph(graph: TeamTaskGraph) -> None:
    _validate_task_ids(graph)
    by_id = {task.task_id: task for task in graph.tasks}
    _validate_edges(graph, by_id)
    _validate_acyclic(graph, by_id)


def _validate_task_ids(graph: TeamTaskGraph) -> None:
    ids = [task.task_id for task in graph.tasks]
    if any(not task_id for task_id in ids):
        raise ValidationError("Team Task ID 不能为空")
    if len(set(ids)) != len(ids):
        raise ValidationError("Team Task ID 必须唯一")


def _validate_edges(
    graph: TeamTaskGraph,
    by_id: dict[str, TeamTask],
) -> None:
    for task in graph.tasks:
        referenced = task.blocked_by | task.blocks
        missing = sorted(referenced - by_id.keys())
        if missing:
            raise ValidationError(
                f"Team Task {task.task_id} 引用了不存在的节点: {', '.join(missing)}"
            )
        if task.task_id in referenced:
            raise ValidationError(f"Team Task 不能依赖自身: {task.task_id}")
        for blocker_id in task.blocked_by:
            if task.task_id not in by_id[blocker_id].blocks:
                raise ValidationError(f"依赖双向边不一致: {blocker_id} -> {task.task_id}")
        for blocked_id in task.blocks:
            if task.task_id not in by_id[blocked_id].blocked_by:
                raise ValidationError(f"依赖双向边不一致: {task.task_id} -> {blocked_id}")


def _validate_acyclic(
    graph: TeamTaskGraph,
    by_id: dict[str, TeamTask],
) -> None:
    indegree = {task.task_id: len(task.blocked_by) for task in graph.tasks}
    ready = [task_id for task_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        task_id = ready.pop()
        visited += 1
        for blocked_id in by_id[task_id].blocks:
            indegree[blocked_id] -= 1
            if indegree[blocked_id] == 0:
                ready.append(blocked_id)
    if visited != len(graph.tasks):
        raise DependencyCycleError("Team Task Graph 存在依赖环")


def _decode_legacy_graph(
    raw: JsonObject,
    team_id: TeamId,
    path: Path,
) -> TeamTaskGraph:
    tasks = raw.get("tasks")
    if not isinstance(tasks, list):
        raise StateCorruptionError(str(path), detail="旧 Team Task tasks 无效")
    graph = TeamTaskGraph(
        team_id,
        tuple(_decode_task(item, path) for item in tasks),
        schema_version=TEAM_TASK_SCHEMA_VERSION,
        revision=1,
    )
    try:
        _validate_graph(graph)
    except ValidationError as exc:
        raise StateCorruptionError(str(path), detail=str(exc)) from exc
    return graph


def _derive_readiness(graph: TeamTaskGraph) -> TeamTaskGraph:
    by_id = {task.task_id: task for task in graph.tasks}
    tasks = tuple(
        replace(
            task,
            is_ready=all(by_id[blocker].status == "completed" for blocker in task.blocked_by),
        )
        for task in graph.tasks
    )
    return replace(graph, tasks=tasks)
