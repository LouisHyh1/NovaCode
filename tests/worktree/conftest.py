import subprocess
from pathlib import Path

import pytest


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "NovaCode Test")
    git(root, "config", "user.email", "novacode@example.com")
    (root / ".gitignore").write_text(
        ".novacode/config.yaml\n"
        ".novacode/settings.local.yaml\n"
        ".novacode/worktrees/\n"
        ".novacode/worktree_session.json\n"
        ".venv/\n"
        ".env\n",
        encoding="utf-8",
    )
    (root / ".worktreeinclude").write_text("*.env\n", encoding="utf-8")
    (root / "README.md").write_text("main\n", encoding="utf-8")
    (root / ".husky").mkdir()
    (root / ".husky" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "init")

    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-u", "origin", "main")

    (root / ".novacode").mkdir()
    (root / ".novacode" / "config.yaml").write_text("provider: test\n", encoding="utf-8")
    (root / ".novacode" / "settings.local.yaml").write_text("permissions: {}\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".env").write_text("TEST_ONLY=1\n", encoding="utf-8")
    return root
