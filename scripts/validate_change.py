"""本次运行时边界变更的一键本地验证。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    # 只检查评测入口；任务内复制的judge和故意缺陷fixture由各自合同验收。
    frozen_cli = [
        p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "evaluation/frozen").glob("*.py"))
    ]
    evaluation_cli = frozen_cli + [
        p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "evaluation/live").glob("*.py"))
    ]
    commands = (
        ["uv", "lock", "--check"],
        ["uv", "run", "--locked", "ruff", "check", "src", "tests", "scripts"],
        ["uv", "run", "--locked", "ruff", "format", "--check", "src", "tests", "scripts"],
        ["uv", "run", "--locked", "ruff", "check", *evaluation_cli],
        [
            "uv",
            "run",
            "--locked",
            "ruff",
            "format",
            "--check",
            *evaluation_cli,
        ],
        [
            "uv",
            "run",
            "--locked",
            "mypy",
            "--strict",
            "--explicit-package-bases",
            "--follow-imports=silent",
            *frozen_cli,
            "evaluation/live/registry_http.py",
        ],
        [
            "uv",
            "run",
            "--locked",
            "ruff",
            "check",
            "--select",
            "C901",
            *frozen_cli,
            "evaluation/live/registry_http.py",
        ],
        ["uv", "run", "--locked", "python", "scripts/check_boundaries.py"],
        [
            "uv",
            "run",
            "--locked",
            "pytest",
            "-q",
            "tests/architecture",
        ],
        ["uv", "run", "--locked", "pytest", "-q"],
        ["uv", "run", "--locked", "python", "-m", "novacode", "--version"],
        ["uv", "run", "--locked", "python", "-m", "novacode", "--help"],
    )
    for command in commands:
        subprocess.run(
            command, cwd=ROOT, check=True, env={**os.environ, "MYPYPATH": str(ROOT / "src")}
        )


if __name__ == "__main__":
    main()
