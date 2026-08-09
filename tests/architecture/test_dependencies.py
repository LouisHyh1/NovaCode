"""Ports and Adapters 导入方向的可执行约束。"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "novacode"
CORE_TARGETS = (
    SRC / "runtime",
    SRC / "application",
    SRC / "team" / "domain.py",
    SRC / "team" / "ports.py",
)
FORBIDDEN_PREFIXES = (
    "anthropic",
    "mcp",
    "openai",
    "textual",
    "novacode.adapters",
    "novacode.cli",
    "novacode.llm",
    "novacode.tui",
)


def _python_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(target.rglob("*.py"))
    return []


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def test_core_boundaries_do_not_import_adapters() -> None:
    violations: list[str] = []
    for target in CORE_TARGETS:
        for path in _python_files(target):
            for imported in _imports(path):
                if imported.startswith(FORBIDDEN_PREFIXES):
                    relative = path.relative_to(ROOT)
                    violations.append(f"{relative} -> {imported}")
    assert not violations, "禁止的核心依赖:\n" + "\n".join(violations)


def test_repository_ports_do_not_expose_filesystem_types() -> None:
    ports = SRC / "team" / "ports.py"
    if not ports.exists():
        return
    forbidden = {"os", "pathlib", "shutil"}
    violations = sorted(set(_imports(ports)) & forbidden)
    assert not violations, f"Repository Port 暴露文件系统依赖: {violations}"
