"""过期临时 Worktree 清理。"""

import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from novacode.worktree.git import _has_worktree_changes, _run_git
from novacode.worktree.lifecycle import ExitOptions

if TYPE_CHECKING:
    from novacode.worktree.manager import Manager

EPHEMERAL_PATTERN = re.compile(r"^agent-a[0-9a-f]{7}$")


def random_agent_name() -> str:
    return "agent-a" + secrets.token_hex(4)[:7]


async def sweep_stale(manager: "Manager", cutoff: datetime) -> list[str]:
    removed: list[str] = []
    for path in list(Path(manager.worktree_dir).iterdir()):
        name = path.name
        if not path.is_dir() or EPHEMERAL_PATTERN.fullmatch(name) is None:
            continue
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) > cutoff:
                continue
        except OSError:
            continue
        session = manager.current_session()
        if session is not None and Path(session.worktree_path) == path:
            continue
        worktree = manager.get(name)
        if worktree is None or await _has_worktree_changes(path, worktree.head_commit):
            continue
        try:
            unpushed = await _run_git(
                path, "rev-list", "--max-count=1", "HEAD", "--not", "--remotes"
            )
        except (OSError, RuntimeError):
            continue
        if unpushed:
            continue
        await manager.remove(name, ExitOptions(discard_changes=True))
        removed.append(name)
    return removed
