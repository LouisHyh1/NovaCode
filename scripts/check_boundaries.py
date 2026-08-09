"""运行本次变更新增边界的增量质量门。"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "src/novacode/runtime",
    "src/novacode/application",
    "src/novacode/adapters",
    "src/novacode/search",
    "src/novacode/team/domain.py",
    "src/novacode/team/ports.py",
    "src/novacode/team/repository.py",
    "src/novacode/team/task_repository.py",
    "src/novacode/team/mailbox_repository.py",
)


def main() -> None:
    targets = [path for path in TARGETS if (ROOT / path).exists()]
    if not targets:
        raise SystemExit("没有找到边界质量门目标")
    # CI 与 WSL/Windows 交叉验证不共享 SQLite 缓存，避免映射盘锁冲突。
    with tempfile.TemporaryDirectory(prefix="novacode-mypy-") as cache_dir:
        commands = (
            ["ruff", "format", "--check", *targets],
            ["ruff", "check", "--select", "C901,BLE001", *targets],
            ["mypy", "--no-incremental", "--cache-dir", cache_dir, *targets],
        )
        for command in commands:
            subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
