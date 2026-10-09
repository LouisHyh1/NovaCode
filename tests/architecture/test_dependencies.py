"""Ports and Adapters 导入方向的可执行约束。"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "novacode"
CORE_TARGETS = (
    SRC / "runtime",
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


def test_product_modules_do_not_import_evaluation() -> None:
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.is_relative_to(SRC / "evaluation"):
            continue
        for imported in _imports(path):
            if imported == "evaluation" or imported.startswith(
                ("novacode.evaluation", "evaluation.")
            ):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert not violations, "产品核心不得反向依赖评测包:\n" + "\n".join(violations)


def test_agent_delegates_complete_context_and_tool_transactions() -> None:
    path = SRC / "agent" / "__init__.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    agent = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Agent"
    )
    method_names = {
        node.name
        for node in agent.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "self._context_manager.prepare(" in source
    assert "if compact_out.summarized:" in source
    assert "self._tool_runner.run(" in source
    assert method_names.isdisjoint(
        {
            "_manage_input",
            "_execute_batched",
            "_run_side_effect",
            "_execute_and_result",
            "_request_approval",
        }
    )


def test_removed_runtime_seams_cannot_be_reintroduced() -> None:
    removed_paths = (
        SRC / "application" / "session_controller.py",
        SRC / "runtime" / "turn.py",
        SRC / "runtime" / "ports.py",
        SRC / "adapters" / "legacy_agent_turn.py",
    )
    assert not [path.relative_to(ROOT) for path in removed_paths if path.exists()]

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SRC.rglob("*.py"))
        if path not in removed_paths
    )
    assert "SessionController" not in source
    assert "TurnEngine" not in source
    assert "LegacyAgentTurnEngine" not in source
