"""对运行时核心执行严格检查，仅容许阶段 7 开始前已记录的历史诊断。"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "runtime_quality_baseline.json"
TARGETS = (
    "src/novacode/__init__.py",
    "src/novacode/agent",
    "src/novacode/cli.py",
    "src/novacode/cli_team_member.py",
    "src/novacode/command/builtin_team.py",
    "src/novacode/evaluation",
    "src/novacode/hook",
    "src/novacode/llm",
    "src/novacode/memory",
    "src/novacode/permission",
    "src/novacode/privacy.py",
    "src/novacode/runtime",
    "src/novacode/search",
    "src/novacode/session",
    "src/novacode/subagent",
    "src/novacode/task",
    "src/novacode/team",
    "src/novacode/tui",
    "scripts",
)


def scope_at(path: Path, line: int) -> str:
    """以作用域识别诊断，行号移动不算新增，同名错误迁入另一函数仍算新增。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    node: ast.AST = tree
    while True:
        match = next(
            (
                child
                for child in ast.iter_child_nodes(node)
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and child.lineno <= line <= (child.end_lineno or child.lineno)
            ),
            None,
        )
        if match is None:
            return ".".join(names) or "<module>"
        names.append(match.name)
        node = match


def diagnostic_key(root: Path, diagnostic: dict[str, Any], gate: str) -> str:
    filename = diagnostic["file"] if gate == "mypy" else diagnostic["filename"]
    path = Path(filename)
    if not path.is_absolute():
        path = root / path
    line = diagnostic["line"] if gate == "mypy" else diagnostic["location"]["row"]
    return "|".join(
        (
            path.relative_to(root).as_posix(),
            scope_at(path, line),
            diagnostic["code"],
            diagnostic["message"],
        )
    )


def collect(root: Path, gate: str, cache_dir: str) -> Counter[str]:
    commands = {
        "mypy": [
            "mypy",
            "--strict",
            "--no-incremental",
            "--follow-imports=silent",
            "--cache-dir",
            cache_dir,
            "--output",
            "json",
        ],
        "C901": ["ruff", "check", "--select", "C901", "--output-format", "json"],
    }
    result = subprocess.run(
        [*commands[gate], *TARGETS], cwd=root, text=True, capture_output=True, encoding="utf-8"
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stdout + result.stderr)
    records = (
        [json.loads(line) for line in result.stdout.splitlines()]
        if gate == "mypy"
        else json.loads(result.stdout)
    )
    errors = [record for record in records if record.get("severity", "error") == "error"]
    if result.returncode and not errors:
        raise RuntimeError(result.stdout + result.stderr)
    return Counter(diagnostic_key(root, record, gate) for record in errors)


def main() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["targets"] == list(TARGETS), "门禁范围改变后需单独审查历史基线"
    subprocess.run(["ruff", "format", "--check", *TARGETS], cwd=ROOT, check=True)
    # 保留原有无历史债务模块的宽泛异常检查，不要求本次清零其他模块。
    subprocess.run(
        [
            "ruff",
            "check",
            "--select",
            "BLE001",
            "src/novacode/search",
            "src/novacode/team/domain.py",
            "src/novacode/team/ports.py",
            "src/novacode/team/repository.py",
            "src/novacode/team/task_repository.py",
            "src/novacode/team/mailbox_repository.py",
        ],
        cwd=ROOT,
        check=True,
    )
    failed = False
    # 使用独立缓存，避免 WSL/Windows 共享映射盘上的 mypy 缓存锁。
    with tempfile.TemporaryDirectory(prefix="novacode-mypy-") as cache_dir:
        for gate in ("mypy", "C901"):
            current = collect(ROOT, gate, cache_dir)
            historical = baseline[gate][sys.platform] if gate == "mypy" else baseline[gate]
            previous = Counter(historical)
            introduced = current - previous
            print(
                f"{gate}: baseline={previous.total()}, current={current.total()}, "
                f"new={introduced.total()}"
            )
            for key, count in sorted(introduced.items()):
                print(f"  {count} x {key}")
            failed |= bool(introduced)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
