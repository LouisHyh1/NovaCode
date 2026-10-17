"""Worktree 使用的 Git 子进程与纯文件系统恢复函数。"""

import asyncio
import os
from pathlib import Path


async def _run_git(work_dir: str | Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="")
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(work_dir),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed ({proc.returncode})")
    return stdout.decode("utf-8", errors="replace").rstrip("\r\n")


async def _has_worktree_changes(wt_path: str, base_commit: str) -> bool:
    """检测未提交修改或 base 之后的新提交；Git 出错时保守地返回 True。"""
    try:
        status = await _run_git(wt_path, "status", "--porcelain")
        if _has_meaningful_status(wt_path, status):
            return True
        count = await _run_git(wt_path, "rev-list", "--count", f"{base_commit}..HEAD")
        return int(count or "0") > 0
    except (OSError, RuntimeError, ValueError):
        return True


def _has_meaningful_status(wt_path: str, status: str) -> bool:
    setup_links = {"node_modules", ".venv", "vendor"}
    for line in status.splitlines():
        path = line[3:].removesuffix("/") if len(line) >= 4 else ""
        if line.startswith("?? ") and path in setup_links:
            if (Path(wt_path) / path).is_symlink():
                continue
        return True
    return False


def _git_dir_from_file(wt_path: str | Path) -> Path | None:
    git_file = Path(wt_path) / ".git"
    try:
        marker, value = git_file.read_text(encoding="utf-8").strip().split(":", 1)
    except (OSError, ValueError):
        return None
    if marker.strip().lower() != "gitdir":
        return None
    git_dir = Path(value.strip())
    if not git_dir.is_absolute():
        git_dir = (git_file.parent / git_dir).resolve()
    return git_dir


def _common_dir(git_dir: Path) -> Path:
    try:
        raw = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return git_dir
    common = Path(raw)
    return common if common.is_absolute() else (git_dir / common).resolve()


def _read_ref(common_dir: Path, ref: str) -> str | None:
    try:
        value = (common_dir / ref).read_text(encoding="ascii").strip()
        if value:
            return value
    except OSError:
        pass
    try:
        lines = (common_dir / "packed-refs").read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line or line[0] in "#^":
            continue
        try:
            sha, name = line.split(" ", 1)
        except ValueError:
            continue
        if name == ref:
            return sha
    return None


def _resolve_worktree_state_from_fs(wt_path: str | Path) -> tuple[str, str] | None:
    """不启动 Git 子进程，返回 linked worktree 的 (HEAD SHA, branch)。"""
    git_dir = _git_dir_from_file(wt_path)
    if git_dir is None:
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
    except OSError:
        return None
    if not head.startswith("ref: "):
        return (head, "") if head else None
    ref = head[5:].strip()
    sha = _read_ref(_common_dir(git_dir), ref)
    if not sha:
        return None
    branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ""
    return sha, branch


def _resolve_head_sha_from_fs(wt_path: str | Path) -> str | None:
    state = _resolve_worktree_state_from_fs(wt_path)
    return state[0] if state is not None else None
