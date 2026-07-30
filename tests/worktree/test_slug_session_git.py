import asyncio
import os
from pathlib import Path

import pytest

from novacode.worktree.git import (
    _has_worktree_changes,
    _resolve_head_sha_from_fs,
    _run_git,
)
from novacode.worktree.session import WorktreeSession, load_session, save_session
from novacode.worktree.slug import flat_slug, validate_slug

from .conftest import git


@pytest.mark.parametrize("name", ["alice", "team/alice", "v1.0", "a_b", "a-b"])
def test_validate_slug_accepts_safe_names(name: str) -> None:
    validate_slug(name)


@pytest.mark.parametrize(
    "name",
    ["", "a" * 65, "..", "./x", "../etc", "a//b", "/x", "a/", "a b", "a;b"],
)
def test_validate_slug_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError):
        validate_slug(name)


def test_flat_slug() -> None:
    assert flat_slug("team/alice") == "team+alice"


def test_session_round_trip_and_null(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    session = WorktreeSession("/a", "/b", "name", "main", "abc", "id")
    save_session(path, session)
    assert load_session(path) == session
    assert set(__import__("json").loads(path.read_text())) == {
        "original_cwd",
        "worktree_path",
        "worktree_name",
        "original_branch",
        "original_head_commit",
        "session_id",
        "hook_based",
    }
    save_session(path, None)
    assert path.read_text(encoding="utf-8") == "null"
    assert load_session(path) is None


@pytest.mark.asyncio
async def test_git_helpers_detect_changes_and_resolve_common_ref(git_repo: Path) -> None:
    worktree = git_repo / ".novacode" / "worktrees" / "helper"
    git(git_repo, "worktree", "add", "-b", "worktree-helper", str(worktree), "HEAD")
    head = git(git_repo, "rev-parse", "HEAD")
    assert _resolve_head_sha_from_fs(worktree) == head
    assert await _has_worktree_changes(str(worktree), head) is False
    (worktree / "changed.txt").write_text("changed", encoding="utf-8")
    assert await _has_worktree_changes(str(worktree), head) is True
    assert await _has_worktree_changes(str(worktree / "missing"), head) is True


@pytest.mark.asyncio
async def test_run_git_disables_prompts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = {}

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    async def fake(*args, **kwargs):
        captured.update(kwargs)
        captured["args"] = args
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    assert await _run_git(tmp_path, "status") == "ok"
    assert captured["stdin"] is asyncio.subprocess.DEVNULL
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GIT_ASKPASS"] == ""
    assert captured["cwd"] == str(tmp_path)
    assert os.environ is not captured["env"]
