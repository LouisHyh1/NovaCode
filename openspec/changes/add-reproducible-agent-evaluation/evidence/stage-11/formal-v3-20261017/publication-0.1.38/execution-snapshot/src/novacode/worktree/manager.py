"""Git Worktree 管理器。"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from novacode.worktree.git import _resolve_worktree_state_from_fs
from novacode.worktree.session import WorktreeSession, clear_session, load_session

DEFAULT_SYMLINK_DIRS = ["node_modules", ".venv", "vendor"]
_EPHEMERAL = re.compile(r"^agent-a[0-9a-f]{7}$")


@dataclass
class Worktree:
    name: str
    path: str
    branch: str
    based_on: str
    head_commit: str
    created: datetime
    manual: bool


class Manager:
    def __init__(self, repo_root: str | Path) -> None:
        root = Path(repo_root).resolve()
        check = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0 or Path(check.stdout.strip()).resolve() != root:
            raise ValueError(f"不是 Git 仓库根目录: {root}")

        self.repo_root = str(root)
        self.worktree_dir = root / ".novacode" / "worktrees"
        self.session_file = root / ".novacode" / "worktree_session.json"
        self.symlink_dirs = list(DEFAULT_SYMLINK_DIRS)
        self.lock = asyncio.Lock()
        self.active: dict[str, Worktree] = {}
        self._creating: set[str] = set()
        self._removing: set[str] = set()
        self._session: WorktreeSession | None = None
        self.worktree_dir.mkdir(parents=True, exist_ok=True)
        self._load_session()
        self._scan_active()
        self._warn_gitignore(root)

    def _load_session(self) -> None:
        try:
            session = load_session(self.session_file)
        except (OSError, TypeError, ValueError) as exc:
            print(f"worktree: 会话文件损坏，已清空: {exc}", file=sys.stderr)
            try:
                clear_session(self.session_file)
            except OSError as clear_exc:
                print(f"worktree: 无法清空会话文件: {clear_exc}", file=sys.stderr)
            return
        if session is not None and not Path(session.worktree_path).is_dir():
            print("worktree: session worktree gone, cleared", file=sys.stderr)
            try:
                clear_session(self.session_file)
            except OSError as exc:
                print(f"worktree: 无法清空会话文件: {exc}", file=sys.stderr)
            return
        self._session = session

    def _scan_active(self) -> None:
        for path in self.worktree_dir.iterdir():
            if not path.is_dir():
                continue
            state = _resolve_worktree_state_from_fs(path)
            if state is None:
                continue
            sha, branch = state
            name = path.name.replace("+", "/")
            self.active[name] = Worktree(
                name=name,
                path=str(path.resolve()),
                branch=branch or f"worktree-{path.name}",
                based_on=sha,
                head_commit=sha,
                created=datetime.fromtimestamp(path.stat().st_mtime),
                manual=_EPHEMERAL.fullmatch(name) is None,
            )

    @staticmethod
    def _warn_gitignore(root: Path) -> None:
        try:
            text = (root / ".gitignore").read_text(encoding="utf-8")
        except OSError:
            text = ""
        required = (".novacode/worktrees/", ".novacode/worktree_session.json")
        if any(line not in text.splitlines() for line in required):
            print("worktree: .gitignore 未忽略 Worktree 运行文件", file=sys.stderr)

    def list(self) -> list[Worktree]:
        return sorted(self.active.values(), key=lambda item: item.name)

    def get(self, name: str) -> Worktree | None:
        return self.active.get(name)

    def current_session(self) -> WorktreeSession | None:
        return self._session

    async def create(self, name: str, base_ref: str = "HEAD", manual: bool = False) -> Worktree:
        from novacode.worktree.create import create

        return await create(self, name, base_ref, manual)

    async def enter(self, name: str) -> WorktreeSession:
        from novacode.worktree.lifecycle import enter

        return await enter(self, name)

    async def exit(self, name, action, opts):
        from novacode.worktree.lifecycle import exit_worktree

        return await exit_worktree(self, name, action, opts)

    async def remove(self, name, opts):
        from novacode.worktree.lifecycle import remove

        return await remove(self, name, opts)

    async def auto_cleanup(self, name):
        from novacode.worktree.lifecycle import auto_cleanup

        return await auto_cleanup(self, name)

    async def sweep_stale(self, cutoff: datetime) -> list[str]:
        from novacode.worktree.sweep import sweep_stale

        return await sweep_stale(self, cutoff)
