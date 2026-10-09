"""Team 派生继承当前 Provider，子进程拥有自己的关闭事务。"""

import asyncio
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from novacode.agent import Agent
from novacode.agent.team_hook import TeamSpawnRequest
from novacode.cli import _amain, _parse_team_member_args
from novacode.cli_team_member import run_team_member
from novacode.config import Config, ProviderConfig, load
from novacode.conversation import Conversation
from novacode.hook import Engine
from novacode.subagent import load_catalog
from novacode.team import BackendType, Manager, TeammateInfo
from novacode.team.backend.tmux import build_member_cmd
from novacode.team.mailbox import Box, Message
from novacode.team.persistence import write_member_config
from novacode.team.registry import AgentRunRegistry
from novacode.tool import Registry


class Provider:
    name = "parent"
    model = "test"
    close_count = 0

    async def close(self):
        self.close_count += 1


class FakeWorktrees:
    async def create(self, name, base_ref, manual):
        return SimpleNamespace(path=str(self.root / name.replace("/", "+")), branch="test")

    async def remove(self, name, options):
        pass


@pytest.mark.asyncio
async def test_missing_explicit_member_config_does_not_fall_back(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing.yaml"
    args = SimpleNamespace(worktree=str(tmp_path), config=str(missing))
    monkeypatch.setattr("novacode.cli._parse_team_member_args", lambda argv: args)
    monkeypatch.setattr("novacode.cli.os.chdir", lambda path: None)
    monkeypatch.setattr("novacode.cli.os.getcwd", lambda: str(tmp_path))
    assert await _amain() == 1
    assert str(missing) in capsys.readouterr().err


@pytest.mark.asyncio
async def test_tmux_spawn_persists_selected_parent_and_cleans_launch_file(tmp_path, monkeypatch):
    monkeypatch.setattr("novacode.team.manager.detect", lambda: BackendType.TMUX)
    worktrees = FakeWorktrees()
    worktrees.root = tmp_path
    manager = Manager(tmp_path / "home", tmp_path, worktrees, None, AgentRunRegistry())
    manager.configure_spawn(load_catalog(tmp_path), fork_teammate=True)
    await manager.create("demo")
    parent = Agent(Provider(), Registry(), context_compression=False)
    first = ProviderConfig("first", "anthropic", "other-key", "other-model")
    selected = ProviderConfig(
        "selected", "openai", "parent-key", "test", "https://parent.invalid", True, 80000
    )
    config = Config(providers=[first, selected])
    manager.bind_provider(parent.provider, config.providers[1])
    captured = []

    class Backend:
        async def spawn(self, request):
            captured.append(request)
            assert load(request.config_path).providers == [selected]
            assert load(request.config_path).features.context_compression is False
            return "", request.agent_id

        async def kill(self, *args):
            pass

    monkeypatch.setattr("novacode.team.manager.new_backend", lambda *a, **k: Backend())
    result = json.loads(
        await manager.spawn_teammate(
            TeamSpawnRequest(
                "demo",
                "alice",
                "work",
                "work",
                caller_agent=parent,
                caller_conversation=Conversation(),
            )
        )
    )
    request = captured[0]
    path = Path(request.config_path)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent == tmp_path / ".novacode" / "sessions"
    assert load(str(path)).features.fork_teammate is True
    args = _parse_team_member_args(build_member_cmd(request)[3:])
    assert args.config == str(path)
    assert "parent-key" not in build_member_cmd(request)
    assert result["backend"] == "tmux"
    await manager.remove_member("demo", "alice", force=True)
    assert not path.exists()
    assert parent.provider.close_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_child_uses_frozen_parent_config_and_closes_on_startup_failure(
    tmp_path, monkeypatch, failure
):
    manager = Manager(tmp_path / "home", tmp_path, None, None, AgentRunRegistry())
    team = await manager.create("demo")
    await team.add_member(TeammateInfo("alice", "child", backend_type=BackendType.TMUX))
    selected = ProviderConfig("parent", "openai", "parent-key", "test")
    path = tmp_path / "launch.yaml"
    write_member_config(path, selected, fork_teammate=False, context_compression=False)
    cfg = load(str(path))
    captured = []
    provider = Provider()

    def create(config):
        captured.append(config)
        return provider

    monkeypatch.setattr("novacode.llm.new_provider", create)
    session = tmp_path / "child.jsonl"
    if failure:

        def fail_writer(*args):
            raise OSError("writer unavailable")

        monkeypatch.setattr("novacode.cli_team_member.SessionWriter", fail_writer)
    else:
        await Box(team.mailbox_dir).write("child", Message(from_="lead", text="work"))

    async def finish(agent, conversation, task):
        assert agent.provider is provider
        assert agent.context_compression is False
        assert agent.teammate_context.team_id == team.team_id
        shutil.rmtree(team.config_dir)

    monkeypatch.setattr("novacode.cli_team_member._print_events", finish)
    monkeypatch.setattr("novacode.cli_team_member.sys.stdin", SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(asyncio.get_running_loop(), "add_reader", lambda *args: None)
    monkeypatch.setattr(asyncio.get_running_loop(), "remove_reader", lambda *args: None)
    args = SimpleNamespace(
        team="demo",
        member="alice",
        agent_id="child",
        session_dir=str(session),
        agent_type="general-purpose",
        plan_mode=False,
        worktree=str(tmp_path),
    )
    kwargs = dict(
        config=cfg,
        registry=Registry(),
        team_manager=manager,
        catalog=load_catalog(tmp_path),
        engine=None,
        hook_engine=Engine([], {}),
    )
    kwargs["config"] = Config(providers=[replace(selected, name="wrong"), selected])
    with pytest.raises(RuntimeError, match="只包含父 Provider"):
        await run_team_member(args, **kwargs)
    assert captured == []
    kwargs["config"] = cfg
    if failure:
        with pytest.raises(OSError, match="writer unavailable"):
            await run_team_member(args, **kwargs)
    else:
        await run_team_member(args, **kwargs)
    assert captured == [selected]
    assert provider.close_count == 1
