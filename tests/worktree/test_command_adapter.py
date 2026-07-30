from pathlib import Path

import pytest

from novacode.command import NopUI, WorktreeSummary
from novacode.command.builtin_worktree import handle_worktree
from novacode.tui.worktree_adapter import WorktreeAdapter


class Accessor:
    def __init__(self) -> None:
        self.calls = []

    async def create(self, name):
        self.calls.append(("create", name))
        return f"/wt/{name}", f"worktree-{name}"

    def list(self):
        self.calls.append(("list",))
        return [WorktreeSummary("demo", "/wt/demo", "worktree-demo", True, True)]

    async def enter(self, name):
        self.calls.append(("enter", name))
        return f"/wt/{name}"

    async def exit(self, action, discard):
        self.calls.append(("exit", action, discard))
        return action == "remove"

    async def remove(self, name, discard):
        self.calls.append(("remove", name, discard))


class UI(NopUI):
    def __init__(self, args: str, accessor=None) -> None:
        self.args = args
        self.accessor = accessor
        self.messages = []
        self.errors = []

    def command_args(self) -> str:
        return self.args

    def worktree_accessor(self):
        return self.accessor

    def println(self, message: str) -> None:
        self.messages.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "call"),
    [
        ("create demo", ("create", "demo")),
        ("list", ("list",)),
        ("enter demo", ("enter", "demo")),
        ("exit --remove --discard", ("exit", "remove", True)),
        ("remove demo --discard", ("remove", "demo", True)),
    ],
)
async def test_handle_worktree_dispatches(args: str, call: tuple) -> None:
    accessor = Accessor()
    ui = UI(args, accessor)
    await handle_worktree(ui)
    assert call in accessor.calls
    assert ui.messages


@pytest.mark.asyncio
async def test_handle_worktree_reports_disabled_manager() -> None:
    ui = UI("list")
    await handle_worktree(ui)
    assert ui.errors == ["当前目录未启用 Worktree 管理"]


class AdapterManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._session = None
        self.item = SimpleWorktree("demo", str(path), "worktree-demo", True)

    async def create(self, name, base, manual):
        return self.item

    def current_session(self):
        return self._session

    def list(self):
        return [self.item]

    async def enter(self, name):
        self._session = SimpleSession(name, str(self.path), "/parent")
        return self._session

    async def exit(self, name, action, options):
        self._session = None
        return type("Report", (), {"removed": action.value == "remove"})()

    async def remove(self, name, options):
        return None


class SimpleWorktree:
    def __init__(self, name, path, branch, manual):
        self.name, self.path, self.branch, self.manual = name, path, branch, manual


class SimpleSession:
    def __init__(self, name, path, original):
        self.worktree_name = name
        self.worktree_path = path
        self.original_cwd = original


@pytest.mark.asyncio
async def test_worktree_adapter_updates_active_cwd(tmp_path: Path) -> None:
    manager = AdapterManager(tmp_path)
    active = []
    adapter = WorktreeAdapter(manager, active.append)
    assert await adapter.enter("demo") == str(tmp_path)
    assert active[-1] == str(tmp_path)
    assert adapter.list()[0].active is True
    assert await adapter.exit("keep", False) is False
    assert active[-1] == "/parent"
