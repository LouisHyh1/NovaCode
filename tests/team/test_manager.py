import json
from pathlib import Path

import pytest

from novacode.team import (
    BackendType,
    Manager,
    TeamHasActiveMembersError,
    TeammateInfo,
)
from novacode.team.persistence import atomic_write_json
from novacode.team.registry import AgentNameRegistry


class FakeTaskManager:
    async def stop(self, agent_id: str) -> bool:
        return True


class FakeWorktreeManager:
    def __init__(self) -> None:
        self.removed = []

    async def remove(self, name, options) -> None:
        self.removed.append((name, options.discard_changes))


@pytest.mark.asyncio
async def test_create_sanitize_suffix_restore_and_delete(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("novacode.team.manager.detect", lambda: BackendType.IN_PROCESS)
    home = tmp_path / "home"
    root = tmp_path / "repo"
    root.mkdir()
    worktrees = FakeWorktreeManager()
    registry = AgentNameRegistry()
    manager = Manager(home, root, worktrees, FakeTaskManager(), registry)

    first = await manager.create("foo bar/baz", "first")
    second = await manager.create("foo bar/baz", "second")

    assert first.sanitized_name == "foo-bar-baz"
    assert second.sanitized_name == "foo-bar-baz-2"
    assert Path(first.config_path).is_file()
    assert (
        json.loads(Path(first.config_path).read_text(encoding="utf-8"))["backend"] == "in-process"
    )

    restored = Manager(home, root, worktrees, FakeTaskManager(), AgentNameRegistry())
    assert [team.sanitized_name for team in restored.list()] == [
        "foo-bar-baz",
        "foo-bar-baz-2",
    ]
    await restored.delete(first.sanitized_name)
    assert not Path(first.config_dir).exists()


@pytest.mark.asyncio
async def test_active_member_blocks_delete_and_reload_before_update(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("novacode.team.manager.detect", lambda: BackendType.IN_PROCESS)
    root = tmp_path / "repo"
    root.mkdir()
    manager = Manager(
        tmp_path / "home",
        root,
        FakeWorktreeManager(),
        FakeTaskManager(),
        AgentNameRegistry(),
    )
    team = await manager.create("demo")
    stale_copy = type(team).from_dict(team.to_dict(), team.config_dir)
    await team.add_member(TeammateInfo("alice", "agent-a", is_active=True))

    with pytest.raises(TeamHasActiveMembersError):
        await manager.delete("demo")

    await stale_copy.set_member_active("alice", False)
    disk = json.loads(Path(team.config_path).read_text(encoding="utf-8"))
    alice = next(item for item in disk["members"] if item["name"] == "alice")
    assert alice["is_active"] is False


def test_corrupt_team_config_is_skipped(tmp_path, capsys) -> None:
    directory = tmp_path / "home" / ".novacode" / "teams" / "broken"
    directory.mkdir(parents=True)
    (directory / "config.json").write_text("{", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    manager = Manager(
        tmp_path / "home",
        root,
        FakeWorktreeManager(),
        FakeTaskManager(),
        AgentNameRegistry(),
    )
    assert manager.list() == []
    assert "跳过损坏配置" in capsys.readouterr().err


def test_atomic_json_round_trip(tmp_path) -> None:
    path = tmp_path / "value.json"
    atomic_write_json(path, {"中文": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"中文": True}
