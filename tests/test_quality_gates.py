"""验证增量门禁检测真实诊断，而不是只看错误总数。"""

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("quality_gate", ROOT / "scripts/check_boundaries.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_targets_include_changed_runtime_and_live_core_files() -> None:
    for target in gate.TARGETS:
        path = ROOT / target
        assert path.is_file() or list(path.rglob("*.py")), target
    for name in (
        "agent",
        "cli.py",
        "cli_team_member.py",
        "command/builtin_team.py",
        "hook",
        "llm",
        "memory",
        "permission",
        "runtime",
        "session",
        "subagent",
        "task",
        "team",
        "tui",
        "search",
    ):
        assert "src/novacode/" + name in gate.TARGETS
    assert "scripts" in gate.TARGETS


def test_strict_mypy_baseline_preserves_scope_and_detects_new_errors(tmp_path, monkeypatch) -> None:
    path = tmp_path / "sample.py"
    source = "def first(value: int) -> str:\n    return value\n"
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(gate, "TARGETS", ("sample.py",))
    baseline = gate.collect(tmp_path, "mypy", str(tmp_path / "cache"))
    assert baseline.total() == 1
    path.write_text("\n\n" + source, encoding="utf-8")
    shifted = gate.collect(tmp_path, "mypy", str(tmp_path / "cache"))
    assert shifted == baseline
    # 原诊断消失，但同样错误迁入新函数，总数相同也应失败。
    path.write_text(source.replace("first", "second"), encoding="utf-8")
    replaced = gate.collect(tmp_path, "mypy", str(tmp_path / "cache"))
    assert (replaced - baseline).total() == 1
    path.write_text("def missing_annotation(value):\n    return value\n", encoding="utf-8")
    strict = gate.collect(tmp_path, "mypy", str(tmp_path / "cache"))
    assert any("no-untyped-def" in key for key in strict)


def test_complexity_gate_catches_new_or_increased_complexity(tmp_path, monkeypatch) -> None:
    path = tmp_path / "sample.py"
    source = "def branch(value):\n"
    source += "".join(f"    if value == {index}:\n        return {index}\n" for index in range(11))
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(gate, "TARGETS", ("sample.py",))
    baseline = gate.collect(tmp_path, "C901", str(tmp_path / "cache"))
    assert (baseline - Counter()).total() == 1
    path.write_text(source + "    if value == 99:\n        return 99\n", encoding="utf-8")
    current = gate.collect(tmp_path, "C901", str(tmp_path / "cache"))
    assert (current - baseline).total() == 1


def test_checker_failure_cannot_pass_as_empty_diagnostics(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gate, "TARGETS", ("does_not_exist.py",))
    with pytest.raises(RuntimeError):
        gate.collect(tmp_path, "mypy", str(tmp_path / "cache"))
