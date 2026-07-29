"""后台任务查询与控制工具。"""

import json
from dataclasses import asdict

from novacode.task.manager import Manager, TaskBusy, TaskNotFound
from novacode.tool import Result


def _args(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _summary(task) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "status": task.status.value,
        "tool_count": task.tool_count,
        "last_activity": task.last_activity,
    }


def _detail(task) -> dict:
    return {
        **_summary(task),
        "task": task.task,
        "result": task.result,
        "err": str(task.err) if task.err is not None else "",
        "start_time": task.start_time,
        "end_time": task.end_time,
        "usage": asdict(task.usage),
    }


class _SystemTool:
    read_only = True

    @property
    def is_system(self) -> bool:
        return True


class TaskListTool(_SystemTool):
    def __init__(self, manager: Manager) -> None:
        self.manager = manager

    def name(self) -> str:
        return "TaskList"

    def description(self) -> str:
        return "列出当前会话的后台 SubAgent 任务。"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, args: str) -> Result:
        content = json.dumps([_summary(item) for item in self.manager.list()], ensure_ascii=False)
        return Result(content)


class TaskGetTool(_SystemTool):
    def __init__(self, manager: Manager) -> None:
        self.manager = manager

    def name(self) -> str:
        return "TaskGet"

    def description(self) -> str:
        return "按 task_id 查询后台任务完整状态。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        task_id = str(_args(args).get("task_id") or "")
        task = self.manager.get(task_id)
        if task is None:
            return Result(f"未知 task_id: {task_id}", is_error=True)
        return Result(json.dumps(_detail(task), ensure_ascii=False))


class TaskStopTool(_SystemTool):
    read_only = False

    def __init__(self, manager: Manager) -> None:
        self.manager = manager

    def name(self) -> str:
        return "TaskStop"

    def description(self) -> str:
        return "取消一个正在运行的后台任务。"

    def parameters(self) -> dict:
        return TaskGetTool(self.manager).parameters()

    async def execute(self, args: str) -> Result:
        task_id = str(_args(args).get("task_id") or "")
        if not await self.manager.stop(task_id):
            return Result(f"任务不存在或未在运行: {task_id}", is_error=True)
        return Result(json.dumps({"status": "cancellation_requested"}))


class SendMessageTool(_SystemTool):
    read_only = False

    def __init__(self, manager: Manager) -> None:
        self.manager = manager

    def name(self) -> str:
        return "SendMessage"

    def description(self) -> str:
        return "向已完成且仍在内存中的命名 SubAgent 续派任务。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["name", "message"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        data = _args(args)
        name = str(data.get("name") or "")
        message = str(data.get("message") or "")
        if not name or not message:
            return Result("name 和 message 为必填项", is_error=True)
        try:
            task_id = await self.manager.send_message(name, message)
        except (TaskNotFound, TaskBusy) as exc:
            return Result(f"无法续派任务: {exc}", is_error=True)
        return Result(json.dumps({"task_id": task_id, "status": "resumed"}))
