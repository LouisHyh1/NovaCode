"""停止后的固定 base 补丁；提交、工作树和新增文件合并进同一临时索引。"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from fnmatch import fnmatchcase
from pathlib import Path

from novacode.evaluation.contracts import require

RUNTIME_EXCLUSIONS = (
    ".novacode",
    "__pycache__",
    ".pytest_cache",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
)


def extract_patch(
    root: Path, base: str, initial_ignored: dict[str, str] | None = None
) -> tuple[str, list[str]]:
    with tempfile.TemporaryDirectory(prefix="novacode-index-") as tmp:
        # 提交对象可复用，但执行中的 Agent 可改写的 Git 配置/Hook 不进入主机执行。
        objects = root / ".git" / "objects"
        require(
            not (root / ".git").is_symlink() and not objects.is_symlink() and objects.is_dir(),
            "Git 对象根不可信",
        )
        require(not any(p.is_symlink() for p in objects.rglob("*")), "Git 对象目录含符号链接")
        safe_git = Path(tmp) / "git"
        safe_git.mkdir()
        shutil.copytree(objects, safe_git / "objects", ignore=shutil.ignore_patterns("info"))
        (safe_git / "refs").mkdir()
        (safe_git / "HEAD").write_text("ref: refs/heads/evaluation\n")
        (safe_git / "config").write_text("[core]\nrepositoryformatversion = 0\nbare = false\n")
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(Path(tmp) / "index"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_DIR": str(safe_git),
            "GIT_WORK_TREE": str(root.resolve()),
            "GIT_CONFIG_COUNT": "0",
            "GIT_ATTR_NOSYSTEM": "1",
        }

        def git(*args: str) -> bytes:
            return subprocess.check_output(
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
                cwd=root,
                env=env,
                stderr=subprocess.PIPE,
            )

        require(git("rev-parse", base).decode().strip() == base, "初始 base 不可复核")
        git("read-tree", base)
        unchanged = []
        for name, digest in (initial_ignored or {}).items():
            path = root / name
            if (
                path.is_file()
                and not path.is_symlink()
                and hashlib.sha256(path.read_bytes()).hexdigest() == digest
            ):
                unchanged.append(f":(exclude,literal){name}")
        git(
            "add",
            "--all",
            "--force",
            "--",
            ".",
            *(f":(exclude){p}" for p in (*RUNTIME_EXCLUSIONS, ".git")),
            *unchanged,
        )
        names = git("diff", "--cached", "--name-only", "-z", base).decode().split("\0")
        patch = git("diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", base).decode()
    return patch, [name for name in names if name]


def scope_status(paths: list[str], allowed: tuple[str, ...]) -> str:
    return (
        "passed"
        if all(any(fnmatchcase(path, pattern) for pattern in allowed) for path in paths)
        else "failed"
    )
