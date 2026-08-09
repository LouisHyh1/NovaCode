import json

import pytest

from novacode.team import BackendType, Manager, TeammateInfo
from novacode.team.mailbox import Box
from novacode.team.registry import AgentRunRegistry
from novacode.team.tools.send_message import SendMessageTool


class FakeTaskManager:
    def get(self, _agent_id):
        return None

    async def send_message(self, _agent_id, _content):
        raise AssertionError("活跃 Team 成员不应走 Agent Run 续派")


@pytest.mark.asyncio
async def test_same_member_name_routes_only_inside_selected_team(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("novacode.team.manager.detect", lambda: BackendType.IN_PROCESS)
    project = tmp_path / "project"
    project.mkdir()
    manager = Manager(
        tmp_path / "home",
        project,
        None,
        FakeTaskManager(),
        AgentRunRegistry(),
    )
    first = await manager.create("first")
    second = await manager.create("second")
    await first.add_member(TeammateInfo("alice", "agent-first", is_active=True))
    await second.add_member(TeammateInfo("alice", "agent-second", is_active=True))

    assert manager.resolve_member(first.team_id, "alice").agent_id == "agent-first"
    assert manager.resolve_member(second.team_id, "alice").agent_id == "agent-second"

    tool = SendMessageTool(manager, FakeTaskManager())
    result = await tool.execute(
        json.dumps({"team_name": "first", "to": "alice", "content": "only first"})
    )

    assert result.is_error is False
    assert [item.text for item in await Box(first.mailbox_dir).read("agent-first")] == [
        "only first"
    ]
    assert await Box(second.mailbox_dir).read("agent-second") == []
