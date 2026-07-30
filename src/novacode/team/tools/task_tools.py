"""Team 共享任务工具，并兼容既有后台 Task 查询。"""

import json

from novacode.team.tasks import Filter, Patch, Status, Store, Task
from novacode.team.tools.common import current_teammate_context, parse_args, resolve_team
from novacode.tool import Result


def _background_summary(task) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "status": task.status.value,
        "tool_count": task.tool_count,
        "last_activity": task.last_activity,
    }


class TaskCreateTool:
    read_only = False

    def __init__(self, manager) -> None:
        self.manager = manager

    def name(self) -> str:
        return "TaskCreate"

    def description(self) -> str:
        return "在当前 Team 的共享任务列表中创建任务。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "assignee": {"type": "string"},
                "blocked_by": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        data = parse_args(args)
        title = str(data.get("title") or "")
        if not title:
            return Result("title 为必填项", is_error=True)
        try:
            team = resolve_team(self.manager, data)
            task = Task(
                title=title,
                description=str(data.get("description") or ""),
                assignee=str(data.get("assignee") or ""),
                blocked_by=[str(item) for item in data.get("blocked_by", [])],
            )
            task_id = await Store(team.tasks_path).create(task)
            for blocker in task.blocked_by:
                await Store(team.tasks_path).update(blocker, Patch(add_blocks=[task_id]))
        except Exception as exc:
            return Result(f"Team 任务创建失败: {exc}", is_error=True)
        return Result(json.dumps({"task_id": task_id}, ensure_ascii=False))


class TaskGetTool:
    read_only = True

    def __init__(self, manager, background_manager) -> None:
        self.manager = manager
        self.background_manager = background_manager

    def name(self) -> str:
        return "TaskGet"

    def description(self) -> str:
        return "查询当前 Team 的共享任务，或查询后台 SubAgent 任务。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "team_name": {"type": "string"},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        data = parse_args(args)
        task_id = str(data.get("task_id") or "")
        use_team = current_teammate_context() is not None or bool(data.get("team_name"))
        if not use_team:
            task = self.background_manager.get(task_id)
            if task is not None:
                detail = {**_background_summary(task), "task": task.task, "result": task.result}
                return Result(json.dumps(detail, ensure_ascii=False))
        try:
            team = resolve_team(self.manager, data)
            task = await Store(team.tasks_path).get(task_id)
        except Exception as exc:
            return Result(f"未知 task_id: {task_id} ({exc})", is_error=True)
        return Result(json.dumps(task.to_dict(include_ready=True), ensure_ascii=False))


class TaskListTool:
    read_only = True

    def __init__(self, manager, background_manager) -> None:
        self.manager = manager
        self.background_manager = background_manager

    def name(self) -> str:
        return "TaskList"

    def description(self) -> str:
        return "列出当前 Team 共享任务；Team 外列出后台 SubAgent 任务。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": [item.value for item in Status],
                },
            },
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        data = parse_args(args)
        use_team = current_teammate_context() is not None or bool(data.get("team_name"))
        if not use_team:
            return Result(
                json.dumps(
                    [_background_summary(item) for item in self.background_manager.list()],
                    ensure_ascii=False,
                )
            )
        try:
            team = resolve_team(self.manager, data)
            status = Status(data["status"]) if data.get("status") else None
            tasks = await Store(team.tasks_path).list(Filter(status))
        except Exception as exc:
            return Result(f"Team 任务列表读取失败: {exc}", is_error=True)
        return Result(
            json.dumps([task.to_dict(include_ready=True) for task in tasks], ensure_ascii=False)
        )


class TaskUpdateTool:
    read_only = False

    def __init__(self, manager) -> None:
        self.manager = manager

    def name(self) -> str:
        return "TaskUpdate"

    def description(self) -> str:
        return "更新 Team 共享任务及其双向依赖关系。"

    def parameters(self) -> dict:
        properties = {
            "team_name": {"type": "string"},
            "task_id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string", "enum": [item.value for item in Status]},
            "assignee": {"type": "string"},
        }
        for name in ("add_blocks", "add_blocked_by", "remove_blocks", "remove_blocked_by"):
            properties[name] = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "properties": properties,
            "required": ["task_id"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        data = parse_args(args)
        try:
            team = resolve_team(self.manager, data)
            patch = Patch(
                title=data.get("title"),
                description=data.get("description"),
                status=Status(data["status"]) if data.get("status") else None,
                assignee=data.get("assignee"),
                add_blocks=list(data.get("add_blocks", [])),
                add_blocked_by=list(data.get("add_blocked_by", [])),
                remove_blocks=list(data.get("remove_blocks", [])),
                remove_blocked_by=list(data.get("remove_blocked_by", [])),
            )
            task = await Store(team.tasks_path).update(str(data.get("task_id") or ""), patch)
        except Exception as exc:
            return Result(f"Team 任务更新失败: {exc}", is_error=True)
        return Result(json.dumps(task.to_dict(include_ready=True), ensure_ascii=False))
