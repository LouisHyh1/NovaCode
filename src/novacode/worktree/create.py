"""创建 Worktree 及 best-effort 环境初始化。"""

import fnmatch
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from novacode.worktree.git import _resolve_worktree_state_from_fs, _run_git
from novacode.worktree.slug import flat_slug, validate_slug

if TYPE_CHECKING:
    from novacode.worktree.manager import Manager, Worktree


async def create(manager: "Manager", name: str, base_ref: str, manual: bool) -> "Worktree":
    from novacode.worktree.manager import Worktree

    validate_slug(name)
    async with manager.lock:
        if name in manager.active or name in manager._creating:
            raise ValueError(f"Worktree 已存在或正在创建: {name}")
        manager._creating.add(name)

    flat = flat_slug(name)
    path = manager.worktree_dir / flat
    branch = f"worktree-{flat}"
    try:
        if path.exists():
            state = _resolve_worktree_state_from_fs(path)
            if state is None:
                raise ValueError(f"现有目录不是可恢复的 Git Worktree: {path}")
            head, actual_branch = state
            worktree = Worktree(
                name,
                str(path.resolve()),
                actual_branch or branch,
                base_ref,
                head,
                datetime.fromtimestamp(path.stat().st_mtime),
                manual,
            )
        else:
            try:
                await _run_git(
                    manager.repo_root,
                    "worktree",
                    "add",
                    "-B",
                    branch,
                    str(path),
                    base_ref,
                )
            except Exception:
                shutil.rmtree(path, ignore_errors=True)
                raise
            await _perform_post_creation_setup(Path(manager.repo_root), path, manager.symlink_dirs)
            head = await _run_git(path, "rev-parse", "HEAD")
            worktree = Worktree(
                name,
                str(path.resolve()),
                branch,
                base_ref,
                head,
                datetime.now(),
                manual,
            )

        async with manager.lock:
            manager.active[name] = worktree
        return worktree
    finally:
        async with manager.lock:
            manager._creating.discard(name)


async def _perform_post_creation_setup(
    repo_root: Path, wt_path: Path, symlink_dirs: list[str]
) -> None:
    steps = (
        ("local-config", _copy_local_configs, (repo_root, wt_path)),
        ("git-hooks", _setup_git_hooks, (repo_root, wt_path)),
        ("large-dir-links", _symlink_large_dirs, (repo_root, wt_path, symlink_dirs)),
        ("included-ignored", _copy_included_ignored, (repo_root, wt_path)),
    )
    for name, step, args in steps:
        try:
            await step(*args) if name in {"git-hooks", "included-ignored"} else step(*args)
        except Exception as exc:
            print(f"worktree: setup {name}: {exc}", file=sys.stderr)


def _copy_local_configs(repo_root: Path, wt_path: Path) -> None:
    for relative in (".novacode/config.yaml", ".novacode/settings.local.yaml"):
        source = repo_root / relative
        target = wt_path / relative
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


async def _setup_git_hooks(repo_root: Path, wt_path: Path) -> None:
    hooks_path = ""
    husky = repo_root / ".husky"
    if husky.is_dir():
        hooks_path = str(husky.resolve())
    else:
        try:
            configured = await _run_git(repo_root, "config", "--get", "core.hooksPath")
        except RuntimeError:
            return
        if configured:
            candidate = Path(configured)
            hooks_path = str(
                candidate if candidate.is_absolute() else (repo_root / candidate).resolve()
            )
    if hooks_path:
        await _run_git(wt_path, "config", "core.hooksPath", hooks_path)


def _symlink_large_dirs(repo_root: Path, wt_path: Path, names: list[str]) -> None:
    for name in names:
        source = repo_root / name
        target = wt_path / name
        if source.exists() and not target.exists():
            os.symlink(source, target, target_is_directory=source.is_dir())


async def _copy_included_ignored(repo_root: Path, wt_path: Path) -> None:
    include = repo_root / ".worktreeinclude"
    if not include.is_file():
        return
    patterns = [
        line.strip()
        for line in include.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not patterns:
        return
    ignored = await _run_git(
        repo_root,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
    )
    matched = False
    for relative in ignored.splitlines():
        if not any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
            continue
        source = repo_root / relative
        if not source.is_file():
            continue
        target = wt_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        matched = True
    if not matched:
        print("worktree: .worktreeinclude 未匹配任何 ignored 文件", file=sys.stderr)
