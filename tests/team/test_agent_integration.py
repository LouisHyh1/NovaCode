import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from novacode.agent import Agent
from novacode.agent.agent_tool import AgentTool
from novacode.agent.team_hook import TeammateContext, TeamSpawnRequest
from novacode.agent.team_mailbox import ingest_team_mailbox
from novacode.conversation import Conversation
from novacode.llm import Request, StreamEvent
from novacode.permission import Mode
from novacode.subagent import load_catalog
from novacode.task import Manager
from novacode.team import BackendType
from novacode.team.mailbox import Box, Message, MessageType
from novacode.team.manager import Manager as TeamManager
from novacode.team.registry import AgentNameRegistry
from novacode.tool import Registry


class Provider:
    name = "test"
    model = "test"

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(text="done")
        yield StreamEvent(done=True)


class Hook:
    def __init__(self) -> None:
        self.request = None

    async def spawn_teammate(self, request) -> str:
        self.request = request
        return json.dumps({"member_name": request.member_name})


@pytest.mark.asyncio
async def test_agent_tool_team_branch_calls_hook(tmp_path) -> None:
    registry = Registry()
    hook = Hook()
    tool = AgentTool(load_catalog(tmp_path), Manager(), team_hook=hook)
    registry.register(tool)
    parent = Agent(Provider(), registry)
    tool.set_parent(parent)
    result = await tool.execute(
        json.dumps(
            {
                "prompt": "work",
                "description": "team work",
                "team_name": "demo",
                "name": "alice",
            }
        )
    )
    assert result.is_error is False
    assert hook.request.team_name == "demo"
    assert hook.request.member_name == "alice"


@pytest.mark.asyncio
async def test_plan_approval_mail_switches_permission_and_adds_reminder(tmp_path) -> None:
    registry = Registry()
    box = Box(tmp_path / "mailbox")
    context = TeammateContext("demo", "alice", "agent-1", "in-process", box, None)
    agent = Agent(Provider(), registry, permission_mode=Mode.PLAN, teammate_context=context)
    await box.write(
        "agent-1",
        Message(
            from_="lead",
            text="go",
            type=MessageType.PLAN_APPROVAL_RESPONSE,
            request_id="req-1",
            approve=True,
        ),
    )
    await ingest_team_mailbox(agent)
    assert agent.permission_mode is Mode.DEFAULT
    assert "<incoming-messages>" in agent.runtime.take_reminders()[0]


class FakeWorktrees:
    async def create(self, name, base_ref, manual):
        return SimpleNamespace(
            path=str(self.root / name.replace("/", "+")),
            branch=f"worktree-{name.replace('/', '+')}",
        )

    async def remove(self, name, options):
        return None


@pytest.mark.asyncio
async def test_inprocess_spawn_marks_idle_and_notifies_lead(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("novacode.team.manager.detect", lambda: BackendType.IN_PROCESS)
    root = tmp_path / "repo"
    root.mkdir()
    worktrees = FakeWorktrees()
    worktrees.root = root / ".novacode" / "worktrees"
    worktrees.root.mkdir(parents=True)
    task_manager = Manager()
    names = AgentNameRegistry()
    team_manager = TeamManager(tmp_path / "home", root, worktrees, task_manager, names)
    team_manager.configure_spawn(load_catalog(root))
    task_manager.set_name_registry(names)
    task_manager.on_task_done(team_manager.handle_task_done)
    team = await team_manager.create("demo")
    registry = Registry()
    parent = Agent(Provider(), registry)
    payload = json.loads(
        await team_manager.spawn_teammate(
            TeamSpawnRequest(
                team_name="demo",
                member_name="alice",
                prompt="work",
                description="work",
                subagent_type="general-purpose",
                caller_agent=parent,
                caller_conversation=Conversation(),
            )
        )
    )
    assert payload["backend"] == "in-process"
    task_id = await task_manager.subscribe_done().get()
    assert task_id == payload["agent_id"]
    for _ in range(10):
        if team.member_by_name("alice").is_active is False:
            break
        await asyncio.sleep(0)
    assert team.member_by_name("alice").is_active is False
    _, unread = await Box(team.mailbox_dir).read_unread("lead")
    assert any("[idle] alice" in message.text for message in unread)
    await team_manager.delete("demo", force=True)
