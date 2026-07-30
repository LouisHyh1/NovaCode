import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from novacode.worktree import (
    ExitAction,
    ExitOptions,
    Manager,
    WorktreeHasChangesError,
    random_agent_name,
)

from .conftest import git


@pytest.mark.asyncio
async def test_enter_exit_change_protection_and_discard(git_repo: Path) -> None:
    manager = Manager(git_repo)
    worktree = await manager.create("manual", "HEAD", manual=True)
    before = Path.cwd()
    session = await manager.enter("manual")
    assert Path.cwd() == before
    assert session.worktree_path == worktree.path
    assert manager.current_session() == session
    assert manager.session_file.is_file()

    Path(worktree.path, "changed.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(WorktreeHasChangesError):
        await manager.exit("manual", ExitAction.REMOVE, ExitOptions())
    assert Path(worktree.path).is_dir()
    assert manager.current_session() == session

    report = await manager.exit("manual", ExitAction.REMOVE, ExitOptions(discard_changes=True))
    assert report.removed is True
    assert not Path(worktree.path).exists()
    assert manager.current_session() is None
    assert manager.session_file.read_text(encoding="utf-8") == "null"
    assert "worktree-manual" not in git(git_repo, "branch", "--list")


@pytest.mark.asyncio
async def test_remove_non_current_and_auto_cleanup(git_repo: Path) -> None:
    manager = Manager(git_repo)
    manual = await manager.create("keep-me", "HEAD", manual=True)
    kept = await manager.auto_cleanup("keep-me")
    assert kept.kept is True and kept.path == manual.path
    await manager.remove("keep-me", ExitOptions(discard_changes=True))

    clean = await manager.create("agent-a1234567", "HEAD", manual=False)
    report = await manager.auto_cleanup(clean.name)
    assert report.kept is False
    assert not Path(clean.path).exists()

    dirty = await manager.create("agent-a7654321", "HEAD", manual=False)
    Path(dirty.path, "dirty.txt").write_text("dirty", encoding="utf-8")
    report = await manager.auto_cleanup(dirty.name)
    assert report.kept is True
    await manager.remove(dirty.name, ExitOptions(discard_changes=True))


@pytest.mark.asyncio
async def test_sweep_stale_filters_name_session_and_changes(git_repo: Path) -> None:
    manager = Manager(git_repo)
    removable = await manager.create("agent-a1111111", "HEAD", manual=False)
    dirty = await manager.create("agent-a2222222", "HEAD", manual=False)
    current = await manager.create("agent-a3333333", "HEAD", manual=False)
    manual = await manager.create("manual-stale", "HEAD", manual=True)
    Path(dirty.path, "dirty.txt").write_text("dirty", encoding="utf-8")
    await manager.enter(current.name)
    old = (datetime.now() - timedelta(days=2)).timestamp()
    for item in (removable, dirty, current, manual):
        os.utime(item.path, (old, old))

    removed = await manager.sweep_stale(datetime.now() - timedelta(hours=24))
    assert removed == [removable.name]
    assert manager.get(dirty.name) is not None
    assert manager.get(current.name) is not None
    assert manager.get(manual.name) is not None

    await manager.exit(current.name, ExitAction.KEEP, ExitOptions())
    for name in (dirty.name, current.name, manual.name):
        await manager.remove(name, ExitOptions(discard_changes=True))


def test_random_agent_name_shape() -> None:
    import re

    assert re.fullmatch(r"agent-a[0-9a-f]{7}", random_agent_name())
