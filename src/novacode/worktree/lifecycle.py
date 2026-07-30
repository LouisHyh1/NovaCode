"""Worktree 进入、退出、删除与自动清理。"""

import asyncio
import contextlib
import os
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from novacode.worktree.git import _has_worktree_changes, _run_git
from novacode.worktree.session import WorktreeSession, save_session

if TYPE_CHECKING:
    from novacode.worktree.manager import Manager, Worktree


class ExitAction(StrEnum):
    KEEP = "keep"
    REMOVE = "remove"


@dataclass
class ExitOptions:
    discard_changes: bool = False


@dataclass
class ExitReport:
    removed: bool
    path: str
    branch: str


@dataclass
class AutoCleanupReport:
    kept: bool
    path: str = ""
    branch: str = ""


class WorktreeHasChangesError(RuntimeError):
    """Worktree 有未提交修改或创建后的新提交。"""


async def enter(manager: "Manager", name: str) -> WorktreeSession:
    async with manager.lock:
        worktree = manager.active.get(name)
        if worktree is None:
            raise ValueError(f"Worktree 不存在: {name}")
        if manager._session is not None:
            raise ValueError(f"已进入 Worktree: {manager._session.worktree_name}")

    try:
        branch = await _run_git(manager.repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        head = await _run_git(manager.repo_root, "rev-parse", "HEAD")
    except (OSError, RuntimeError):
        branch = ""
        head = ""
    session = WorktreeSession(
        original_cwd=str(Path.cwd()),
        worktree_path=worktree.path,
        worktree_name=name,
        original_branch=branch,
        original_head_commit=head,
        session_id=str(uuid.uuid4()),
    )
    async with manager.lock:
        manager._session = session
        save_session(manager.session_file, session)
    return session


async def exit_worktree(
    manager: "Manager", name: str, action: ExitAction, opts: ExitOptions
) -> ExitReport:
    async with manager.lock:
        session = manager._session
        worktree = manager.active.get(name)
        if session is None or session.worktree_name != name:
            raise ValueError("只能退出当前 Worktree 会话")
        if worktree is None:
            raise ValueError(f"Worktree 不存在: {name}")

    if action is ExitAction.REMOVE and not opts.discard_changes:
        if await _has_worktree_changes(worktree.path, worktree.head_commit):
            raise WorktreeHasChangesError("Worktree 有未提交修改或新提交，拒绝删除")

    with contextlib.suppress(OSError):
        os.chdir(session.original_cwd)
    async with manager.lock:
        manager._session = None
        save_session(manager.session_file, None)
    if action is ExitAction.REMOVE:
        await _remove_unchecked(manager, worktree)
    return ExitReport(action is ExitAction.REMOVE, worktree.path, worktree.branch)


async def remove(manager: "Manager", name: str, opts: ExitOptions) -> None:
    async with manager.lock:
        worktree = manager.active.get(name)
        if worktree is None:
            raise ValueError(f"Worktree 不存在: {name}")
        if manager._session is not None and manager._session.worktree_name == name:
            raise ValueError("当前 Worktree 请使用 /worktree exit 退出")
    if not opts.discard_changes and await _has_worktree_changes(
        worktree.path, worktree.head_commit
    ):
        raise WorktreeHasChangesError("Worktree 有未提交修改或新提交，拒绝删除")
    await _remove_unchecked(manager, worktree)


async def _remove_unchecked(manager: "Manager", worktree: "Worktree") -> None:
    async with manager.lock:
        if worktree.name in manager._removing:
            raise ValueError(f"Worktree 正在删除: {worktree.name}")
        manager._removing.add(worktree.name)
    removed = False
    try:
        await _run_git(manager.repo_root, "worktree", "remove", "--force", worktree.path)
        removed = True
        await asyncio.sleep(0.1)
        await _run_git(manager.repo_root, "branch", "-D", worktree.branch)
    finally:
        async with manager.lock:
            manager._removing.discard(worktree.name)
            if removed:
                manager.active.pop(worktree.name, None)


async def auto_cleanup(manager: "Manager", name: str) -> AutoCleanupReport:
    async with manager.lock:
        worktree = manager.active.get(name)
    if worktree is None:
        raise ValueError(f"Worktree 不存在: {name}")
    if worktree.manual:
        return AutoCleanupReport(True, worktree.path, worktree.branch)
    if await _has_worktree_changes(worktree.path, worktree.head_commit):
        return AutoCleanupReport(True, worktree.path, worktree.branch)
    await remove(manager, name, ExitOptions(discard_changes=True))
    return AutoCleanupReport(False)
