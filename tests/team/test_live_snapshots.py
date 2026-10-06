"""跨进程更新必须在下一次 lead 操作中可见。"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from novacode.team import BackendType, Manager, TeamHasActiveMembersError
from novacode.team.mailbox import Box
from novacode.team.registry import AgentRunRegistry
from novacode.team.tasks import Store, Task
from novacode.team.tools.send_message import SendMessageTool
from novacode.team.tools.task_tools import TaskGetTool, TaskListTool


class Background:
    def get(self, agent_id):
        return None


@pytest.mark.asyncio
async def test_external_member_and_task_updates_are_visible_to_lead(tmp_path, monkeypatch):
    monkeypatch.setattr("novacode.team.manager.detect", lambda: BackendType.TMUX)
    manager = Manager(tmp_path / "home", tmp_path, None, Background(), AgentRunRegistry())
    old = await manager.create("live")
    task_id = await Store(old.tasks_path, team_id=old.team_id).create(Task(title="work"))
    script = """
import asyncio, sys
from novacode.team.repository import JsonTeamRepository
from novacode.team.persistence import apply_team_state
from novacode.team.types import Team, TeammateInfo, BackendType
from novacode.team.tasks import Store, Patch, Status
async def update():
    team = Team('', '', '', BackendType.TMUX)
    team.set_paths(sys.argv[1])
    apply_team_state(team, await JsonTeamRepository(team.config_path).load_existing())
    await team.add_member(TeammateInfo('alice', 'external-id', backend_type=BackendType.TMUX,
                                     is_active=True))
    await Store(team.tasks_path, team_id=team.team_id).update(
        sys.argv[2], Patch(status=Status.COMPLETED, assignee='external-id'))
asyncio.run(update())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        old.config_dir,
        task_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()

    fresh = await manager.get(old.team_id)
    assert fresh is not old
    assert old.member_by_name("alice") is None
    assert fresh.member_by_name("alice").agent_id == "external-id"
    assert (await manager.resolve_member("live", "alice")).is_active is True
    assert (await manager.team_for_agent("external-id"))[0].team_id == old.team_id
    assert (await manager.list())[0].revision > old.revision
    with pytest.raises(TeamHasActiveMembersError):
        await manager.delete("live")
    assert Path(old.config_path).exists()

    sent = await SendMessageTool(manager, Background()).execute(
        json.dumps({"team_name": "live", "to": "alice", "content": "fresh"})
    )
    assert not sent.is_error, sent.content
    assert [message.text for message in await Box(old.mailbox_dir).read("external-id")] == ["fresh"]
    for tool, payload in (
        (TaskGetTool(manager, Background()), {"team_name": "live", "task_id": task_id}),
        (TaskListTool(manager, Background()), {"team_name": "live"}),
    ):
        result = await tool.execute(json.dumps(payload))
        assert not result.is_error, result.content
        data = json.loads(result.content)
        task = data[0] if isinstance(data, list) else data
        assert task["status"] == "completed"
        assert task["assignee"] == "external-id"

    external = Manager(tmp_path / "home", tmp_path, None, Background(), AgentRunRegistry())
    await external.create("new")
    assert {team.name for team in await manager.list()} == {"live", "new"}
    await external.delete("new")
    assert await manager.get("new") is None
