import json
from types import SimpleNamespace

import pytest

from novacode.team import BackendType
from novacode.team.manager import Manager
from novacode.team.registry import AgentRunRegistry
from novacode.team.tasks import Store, Task
from novacode.team.tools.task_tools import TaskGetTool, TaskListTool


class BackgroundManager:
    def __init__(self) -> None:
        self.run = SimpleNamespace(
            id="run-1",
            name="worker",
            status=SimpleNamespace(value="completed"),
            tool_count=1,
            last_activity="read_file",
            task="inspect",
            result="done",
        )

    def list(self):
        return [self.run]

    def get(self, run_id):
        return self.run if run_id == self.run.id else None


@pytest.mark.asyncio
async def test_task_tools_route_by_explicit_team_context(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("novacode.team.manager.detect", lambda: BackendType.IN_PROCESS)
    project = tmp_path / "project"
    project.mkdir()
    background = BackgroundManager()
    manager = Manager(
        tmp_path / "home",
        project,
        None,
        background,
        AgentRunRegistry(),
    )
    team = await manager.create("demo")
    team_task_id = await Store(team.tasks_path, team_id=team.team_id).create(
        Task(title="team work")
    )
    list_tool = TaskListTool(manager, background)
    get_tool = TaskGetTool(manager, background)

    run_list = json.loads((await list_tool.execute("{}")).content)
    team_list = json.loads((await list_tool.execute(json.dumps({"team_name": "demo"}))).content)
    run_detail = json.loads((await get_tool.execute(json.dumps({"task_id": "run-1"}))).content)
    team_detail = json.loads(
        (await get_tool.execute(json.dumps({"team_name": "demo", "task_id": team_task_id}))).content
    )

    assert run_list[0]["id"] == "run-1"
    assert run_detail["result"] == "done"
    assert team_list[0]["id"] == team_task_id
    assert team_detail["title"] == "team work"
