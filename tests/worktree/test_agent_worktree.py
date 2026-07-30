import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from novacode.agent.agent_tool import AgentTool
from novacode.agent.agent_worktree import (
    build_worktree_notice,
    execute_with_worktree,
)
from novacode.subagent import load_catalog
from novacode.task import Manager as TaskManager
from novacode.tool import cwd_from_ctx, with_cwd
from novacode.tool.write_file import WriteFileTool
from novacode.worktree import AutoCleanupReport


class StubManager:
    def __init__(self, root: Path, *, kept: bool) -> None:
        self.root = root
        self.kept = kept
        self.created = []
        self.cleaned = []

    async def create(self, name, base_ref, manual):
        self.created.append((name, base_ref, manual))
        return SimpleNamespace(path=str(self.root), branch=f"worktree-{name}")

    async def auto_cleanup(self, name):
        self.cleaned.append(name)
        return AutoCleanupReport(
            self.kept,
            str(self.root) if self.kept else "",
            f"worktree-{name}" if self.kept else "",
        )


class StubAgent:
    def __init__(self) -> None:
        self.cwd = None
        self.task = ""

    async def run_to_completion(self, conversation, task, events):
        self.cwd = cwd_from_ctx()
        self.task = task
        return "done"


@pytest.mark.asyncio
async def test_execute_with_worktree_injects_cwd_notice_and_cleanup(tmp_path: Path) -> None:
    manager = StubManager(tmp_path, kept=True)
    agent = StubAgent()
    result = await execute_with_worktree(manager, agent, object(), "do it", None)
    assert manager.created and manager.cleaned
    assert re.fullmatch(r"agent-a[0-9a-f]{7}", manager.created[0][0])
    assert manager.created[0][1:] == ("HEAD", False)
    assert agent.cwd == str(tmp_path)
    assert "<worktree-context>" in agent.task
    assert str(tmp_path) in agent.task
    assert agent.task.endswith("do it")
    assert "Worktree 保留在" in result


@pytest.mark.asyncio
async def test_execute_with_worktree_remaps_parent_absolute_write(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    worktree = tmp_path / "worktree"
    parent.mkdir()
    worktree.mkdir()
    (parent / "witness.txt").write_text("parent", encoding="utf-8")
    (worktree / "witness.txt").write_text("worktree", encoding="utf-8")

    class WritingAgent:
        async def run_to_completion(self, conversation, task, events):
            result = await WriteFileTool().execute(
                json.dumps(
                    {
                        "path": str(parent / "witness.txt"),
                        "content": "isolated",
                    }
                )
            )
            assert not result.is_error
            return "done"

    with with_cwd(str(parent)):
        await execute_with_worktree(
            StubManager(worktree, kept=True), WritingAgent(), object(), "write", None
        )

    assert (parent / "witness.txt").read_text(encoding="utf-8") == "parent"
    assert (worktree / "witness.txt").read_text(encoding="utf-8") == "isolated"


def test_build_worktree_notice_contains_both_paths() -> None:
    notice = build_worktree_notice("/parent", "/child")
    assert "<worktree-context>" in notice
    assert "/parent" in notice and "/child" in notice


def _write_isolated_agent(root: Path, *, background: bool = False) -> None:
    agents = root / ".novacode" / "agents"
    agents.mkdir(parents=True)
    (agents / "isolated.md").write_text(
        "---\n"
        "name: isolated\n"
        "description: isolated test\n"
        "isolation: worktree\n"
        f"background: {'true' if background else 'false'}\n"
        "---\n\nwork",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_agent_tool_reports_missing_worktree_manager(tmp_path: Path) -> None:
    _write_isolated_agent(tmp_path)
    tool = AgentTool(load_catalog(tmp_path), TaskManager(), parent=object())
    result = await tool.execute(
        json.dumps({"prompt": "work", "description": "test", "subagent_type": "isolated"})
    )
    assert result.is_error
    assert "Worktree 管理器未配置" in result.content


@pytest.mark.asyncio
async def test_agent_tool_forces_isolated_background_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_isolated_agent(tmp_path, background=True)
    manager = object()
    tool = AgentTool(
        load_catalog(tmp_path),
        TaskManager(),
        parent=object(),
        bg_enabled=False,
        worktree_mgr=manager,
    )
    observed = {}

    class Child:
        pass

    def new_agent(definition, background):
        observed["background"] = background
        return Child()

    async def isolated(manager_arg, child, conversation, prompt, events):
        observed["manager"] = manager_arg
        return "isolated done"

    monkeypatch.setattr(tool, "_new_agent", new_agent)
    monkeypatch.setattr(
        "novacode.agent.agent_worktree.execute_with_worktree",
        isolated,
    )
    result = await tool.execute(
        json.dumps({"prompt": "work", "description": "test", "subagent_type": "isolated"})
    )
    assert result.content == "isolated done"
    assert observed == {"background": False, "manager": manager}


@pytest.mark.asyncio
async def test_agent_tool_accepts_call_level_worktree_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = object()
    tool = AgentTool(
        load_catalog(tmp_path),
        TaskManager(),
        parent=object(),
        worktree_mgr=manager,
    )
    observed = {}

    class Child:
        async def run_to_completion(self, conversation, prompt, events):
            observed["inline"] = True
            return "not isolated"

    monkeypatch.setattr(tool, "_new_agent", lambda definition, background: Child())

    async def isolated(manager_arg, child, conversation, prompt, events):
        observed["manager"] = manager_arg
        return "isolated done"

    monkeypatch.setattr(
        "novacode.agent.agent_worktree.execute_with_worktree",
        isolated,
    )
    result = await tool.execute(
        json.dumps(
            {
                "prompt": "work",
                "description": "test",
                "subagent_type": "general-purpose",
                "isolation": "worktree",
            }
        )
    )

    assert result.content == "isolated done"
    assert observed == {"manager": manager}
