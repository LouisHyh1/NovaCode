from pathlib import Path

import pytest

from novacode.worktree import ExitAction, ExitOptions, Manager

from .conftest import git


def test_manager_requires_exact_git_root(tmp_path: Path, git_repo: Path) -> None:
    manager = Manager(git_repo)
    assert manager.repo_root == str(git_repo.resolve())
    assert manager.worktree_dir.is_dir()
    with pytest.raises(ValueError):
        Manager(tmp_path)
    with pytest.raises(ValueError):
        Manager(git_repo / "src")


@pytest.mark.asyncio
async def test_create_nested_and_post_creation_setup(git_repo: Path) -> None:
    manager = Manager(git_repo)
    worktree = await manager.create("team/alice", "HEAD", manual=True)
    path = Path(worktree.path)
    assert path == git_repo / ".novacode" / "worktrees" / "team+alice"
    assert worktree.branch == "worktree-team+alice"
    assert git(path, "branch", "--show-current") == worktree.branch
    assert (path / ".novacode" / "config.yaml").read_text() == "provider: test\n"
    assert (path / ".novacode" / "settings.local.yaml").is_file()
    assert (path / ".venv").is_symlink()
    assert (path / ".env").read_text() == "TEST_ONLY=1\n"
    assert git(path, "config", "--get", "core.hooksPath") == str((git_repo / ".husky").resolve())


@pytest.mark.asyncio
async def test_create_active_duplicate_and_fast_recovery(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Manager(git_repo)
    original = await manager.create("alice", "HEAD", manual=True)
    with pytest.raises(ValueError):
        await manager.create("alice", "HEAD", manual=True)

    manager.active.clear()

    async def fail_git(*args, **kwargs):
        raise AssertionError("快速恢复不应启动 Git 子进程")

    monkeypatch.setattr("novacode.worktree.create._run_git", fail_git)
    recovered = await manager.create("alice", "HEAD", manual=True)
    assert recovered.path == original.path
    assert recovered.head_commit == original.head_commit


def test_manager_loads_and_clears_stale_session(git_repo: Path, capsys) -> None:
    manager = Manager(git_repo)
    manager.session_file.write_text(
        '{"original_cwd":"/a","worktree_path":"/missing","worktree_name":"x",'
        '"original_branch":"main","original_head_commit":"a","session_id":"id",'
        '"hook_based":false}',
        encoding="utf-8",
    )
    restored = Manager(git_repo)
    assert restored.current_session() is None
    assert restored.session_file.read_text(encoding="utf-8") == "null"
    assert "session worktree gone" in capsys.readouterr().err


def test_manager_ignores_corrupt_session(git_repo: Path, capsys) -> None:
    manager = Manager(git_repo)
    manager.session_file.write_text("{broken", encoding="utf-8")
    restored = Manager(git_repo)
    assert restored.current_session() is None
    assert restored.session_file.read_text(encoding="utf-8") == "null"
    assert "会话文件损坏" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_manager_restores_active_session(git_repo: Path) -> None:
    manager = Manager(git_repo)
    worktree = await manager.create("resume-me", "HEAD", manual=True)
    session = await manager.enter(worktree.name)

    restored = Manager(git_repo)
    assert restored.current_session() == session
    assert restored.get(worktree.name) is not None
    await restored.exit(worktree.name, ExitAction.KEEP, ExitOptions())
    await restored.remove(worktree.name, ExitOptions(discard_changes=True))
