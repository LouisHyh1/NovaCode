"""停止后的固定 base 补丁；提交、工作树和新增文件合并进同一临时索引。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from novacode.evaluation.contracts import require

RUNTIME_EXCLUSIONS = (
    ".novacode",
    "__pycache__",
    ".pytest_cache",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
)


def business_state(root: Path) -> list[dict[str, str]]:
    """不跟随链接；Git 补丁可表达的业务文件、目标和执行位。"""
    entries = []
    pending = list(root.iterdir())
    while pending:
        path = pending.pop()
        name = path.relative_to(root).as_posix()
        if name == ".git" or name.startswith((".git/", ".novacode/")) or name == ".novacode":
            continue
        if any(part in ("__pycache__", ".pytest_cache") for part in path.relative_to(root).parts):
            continue
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            pending.extend(path.iterdir())
            continue
        require(stat.S_ISREG(mode) or stat.S_ISLNK(mode), "业务状态含不可归档文件类型")
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            entries.append(
                {
                    "path": name,
                    "type": "symlink",
                    "mode": "120000",
                    "target": target,
                    "sha256": hashlib.sha256(os.fsencode(target)).hexdigest(),
                }
            )
        else:
            with path.open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            entries.append(
                {
                    "path": name,
                    "type": "file",
                    "mode": "100755" if mode & stat.S_IXUSR else "100644",
                    "sha256": sha,
                }
            )
    return sorted(entries, key=lambda e: e["path"])


@contextmanager
def isolated_index(root: Path, objects: Path) -> Iterator[tuple[Callable[..., bytes], Path]]:
    with tempfile.TemporaryDirectory(prefix="novacode-index-") as tmp:
        # 提交对象可复用，但执行中的 Agent 可改写的 Git 配置/Hook 不进入主机执行。
        require(
            not objects.parent.is_symlink() and not objects.is_symlink() and objects.is_dir(),
            "Git 对象根不可信",
        )
        require(not any(p.is_symlink() for p in objects.rglob("*")), "Git 对象目录含符号链接")
        safe_git = Path(tmp) / "git"
        safe_git.mkdir()
        shutil.copytree(objects, safe_git / "objects", ignore=shutil.ignore_patterns("info"))
        (safe_git / "refs").mkdir()
        (safe_git / "HEAD").write_text("ref: refs/heads/evaluation\n")
        (safe_git / "config").write_text("[core]\nrepositoryformatversion = 0\nbare = false\n")
        (safe_git / "info").mkdir()
        # 业务字节按原样提取；不执行工作树属性指定的转换或换行处理。
        (safe_git / "info/attributes").write_text("* -text -ident -filter -working-tree-encoding\n")
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

        def git(*args: str, input: bytes | None = None) -> bytes:
            return subprocess.check_output(
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
                cwd=root,
                env=env,
                stderr=subprocess.PIPE,
                input=input,
            )

        yield git, safe_git


def stage_all(git: Callable[..., bytes], unchanged: list[str] | None = None) -> None:
    paths = [".", *(f":(exclude){p}" for p in (*RUNTIME_EXCLUSIONS, ".git")), *(unchanged or [])]
    git(
        "add",
        "--all",
        "--force",
        "--pathspec-from-file=-",
        "--pathspec-file-nul",
        input=b"\0".join(os.fsencode(p) for p in paths) + b"\0",
    )


def capture_baseline(root: Path, base: str, destination: Path) -> dict[str, Any]:
    """在 Agent 启动前生成可信树及对象；保存到 Agent 不可达的评测目录。"""
    destination.mkdir(parents=True, exist_ok=False)
    with isolated_index(root, root / ".git/objects") as (git, safe_git):
        require(git("rev-parse", base).decode().strip() == base, "初始 base 不可复核")
        git("read-tree", base)
        stage_all(git)
        tree = git("write-tree").decode().strip()
        # 固定树与 base 都保留；打包避免每次复制数万松散对象。
        git("update-ref", "refs/heads/evaluation", base)
        git("update-ref", "refs/tags/initial-tree", tree)
        git("-c", "pack.threads=1", "-c", "pack.windowMemory=16m", "repack", "-a", "-d")
        shutil.copytree(safe_git / "objects", destination / "objects")
        info = {
            "schema_version": 2,
            "base_commit": base,
            "tree": tree,
            "entries": business_state(root),
        }
    raw = json.dumps(info, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    (destination / "manifest.json").write_bytes(raw)
    (destination / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    return info


def restore_workspace(baseline: Path, patch: str, destination: Path) -> None:
    """从可信初始对象和补丁实际重建业务状态，拒绝覆盖现有输出。"""
    raw = (baseline / "manifest.json").read_bytes()
    require(
        hashlib.sha256(raw).hexdigest() == (baseline / "manifest.sha256").read_text().strip(),
        "初始基线指纹变化",
    )
    info = json.loads(raw)
    require(info.get("schema_version") == 2, "无类型化初始基线")
    destination.mkdir(parents=True, exist_ok=False)
    with isolated_index(destination, baseline / "objects") as (git, _):
        git("read-tree", info["tree"])
        git("checkout-index", "--all", "--force")
        require(business_state(destination) == info["entries"], "初始基线内容/类型/模式不一致")
        if patch:
            git("apply", "--binary", "-", input=patch.encode())


def extract_patch(
    root: Path, base: str, initial_ignored: Path | dict[str, str] | None = None
) -> tuple[str, list[str]]:
    baseline = initial_ignored if isinstance(initial_ignored, Path) else None
    tree = base
    if baseline is not None:
        raw = (baseline / "manifest.json").read_bytes()
        require(
            hashlib.sha256(raw).hexdigest() == (baseline / "manifest.sha256").read_text().strip(),
            "初始基线指纹变化",
        )
        info = json.loads(raw)
        require(
            info.get("schema_version") == 2 and info.get("base_commit") == base,
            "初始基线 base 不一致",
        )
        tree = info["tree"]
    objects = baseline / "objects" if baseline is not None else root / ".git/objects"
    with isolated_index(root, objects) as (git, _):
        require(git("rev-parse", base).decode().strip() == base, "初始 base 不可复核")
        git("read-tree", tree)
        unchanged = []
        for name, digest in initial_ignored.items() if isinstance(initial_ignored, dict) else ():
            path = root / name
            if (
                path.is_file()
                and not path.is_symlink()
                and hashlib.sha256(path.read_bytes()).hexdigest() == digest
            ):
                unchanged.append(f":(exclude,literal){name}")
            else:
                require(False, "旧仅哈希基线不能表达修改/删除；需要可信类型化初始基线")
        stage_all(git, unchanged)
        names = git("diff", "--cached", "--name-only", "-z", tree).decode().split("\0")
        patch = git("diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", tree).decode()
    return patch, [name for name in names if name]


def scope_status(paths: list[str], allowed: tuple[str, ...]) -> str:
    return (
        "passed"
        if all(any(fnmatchcase(path, pattern) for pattern in allowed) for path in paths)
        else "failed"
    )
