"""本次运行时边界变更的一键本地验证。"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    commands = (
        ["uv", "lock", "--check"],
        ["uv", "run", "--locked", "ruff", "check", "src", "tests"],
        ["uv", "run", "--locked", "ruff", "format", "--check", "src", "tests"],
        ["uv", "run", "--locked", "python", "scripts/check_boundaries.py"],
        [
            "uv",
            "run",
            "--locked",
            "pytest",
            "-q",
            "tests/architecture",
        ],
        ["uv", "run", "--locked", "python", "-m", "novacode", "--version"],
        ["uv", "run", "--locked", "python", "-m", "novacode", "--help"],
    )
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
