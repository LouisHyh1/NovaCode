"""冻结专项负例、计划配对及执行前拒绝缺失身份的机制验收。"""

import json
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from evaluation.frozen.definitions import cases
from evaluation.frozen.judge import grade
from evaluation.frozen.specialty import manifest
from novacode.evaluation.contracts import EvaluationTask
from novacode.evaluation.frozen import plan_runs, source_files, validate_policy
from novacode.evaluation.specialty_tools import connect_records, registry_for

REPO = Path(__file__).resolve().parents[2]
BUDGET_PATH = (
    REPO
    / "openspec/changes/add-reproducible-agent-evaluation"
    / "evidence/stage-9/calibrated-budgets.json"
)


@pytest.mark.parametrize("case", cases(), ids=lambda case: case.task_id)
def test_correct_control_wrong_answer_scope_and_missing_behavior(case):
    data = manifest(case, "unused", "a" * 40, Path("/unused"))
    answer = json.dumps(case.answers[0]) if case.answers else ""
    assert all(grade(data, case.reference, answer, local=True)["conditions"].values())
    assert not all(grade(data, {}, "{}", local=True)["conditions"].values())
    assert not all(
        grade(data, {**case.reference, "forbidden.py": "changed"}, answer, local=True)[
            "conditions"
        ].values()
    )
    if case.answers:
        assert not grade(data, case.reference, '{"reason":"wrong"}', local=True)["conditions"][
            "facts"
        ]


@pytest.mark.parametrize("identity", ["FS05", "FS06", "FS07", "FS08"])
@pytest.mark.parametrize(
    "test",
    [
        "import missing_dependency\n",
        "import unittest\n@unittest.skip('skip')\nclass T(unittest.TestCase):\n"
        "    def test_skip(self): pass\n",
        "import unittest\nclass T(unittest.TestCase):\n    def test_fail(self): self.fail('bad')\n",
    ],
)
def test_invalid_test_addition_is_not_mutant_detection(identity, test):
    case = next(c for c in cases() if c.task_id == identity)
    data = manifest(case, "unused", "a" * 40, Path("/unused"))
    result = grade(data, {"tests/test_added.py": test}, "", local=True)
    assert not all(result["conditions"].values())


def frozen_tasks():
    specialties = tuple(
        EvaluationTask.from_json(p.read_text())
        for p in sorted((REPO / "evaluation/frozen/specialty-assets/contracts").glob("*.json"))
    )
    template = EvaluationTask.from_json(
        (REPO / "evaluation/live/assets/contracts/L01.json").read_text()
    )
    # 仅用于预算/顺序机制，不作为真实Live入库或模型证据。
    live = tuple(
        replace(
            template,
            task_id=f"fixture-live-{i}",
            suite="frozen",
            difficulty="easy" if i < 8 else "medium" if i < 20 else "hard",
            budget_category="live-" + ("easy" if i < 8 else "medium" if i < 20 else "hard"),
        )
        for i in range(24)
    )
    return live + specialties


def test_432_originals_pairing_balance_and_shared_task_budgets():
    tasks = frozen_tasks()
    budgets = json.loads(BUDGET_PATH.read_text())
    plan = plan_runs(tasks, budgets, "fixture", 20261012)
    assert plan == plan_runs(tasks, budgets, "fixture", 20261012)
    assert len(plan) == len({r["run_id"] for r in plan}) == 432
    assert len({(r["task_id"], r["config_id"], r["repeat_id"]) for r in plan}) == 432
    assert Counter(r["config_id"] for r in plan) == {
        "full": 144,
        "no-compression": 144,
        "eager-schema": 144,
    }
    assert set(r["state"] for r in plan) == {"PLANNED"}
    orders = Counter()
    sources = {t.task_id: t.source for t in tasks}
    assert set(Counter((sources[r["task_id"]], r["config_id"]) for r in plan).values()) == {72}
    for index in range(0, len(plan), 3):
        block = plan[index : index + 3]
        assert len({r["pair_key"] for r in block}) == 1
        assert all(r["limits"] == block[0]["limits"] for r in block)
        orders[tuple(r["config_id"] for r in block)] += 1
    assert len(orders) == 6 and set(orders.values()) == {24}


@pytest.mark.parametrize(
    "key",
    [
        "context_window",
        "thinking",
        "sdk_max_retries",
        "roles",
        "permissions",
        "memory",
        "hooks",
        "analysis",
        "instructions",
        "usage_rule",
        "timeout_seconds",
    ],
)
def test_missing_required_freeze_value_is_rejected(key):
    from evaluation.frozen.freeze import policy

    data = policy()
    del data[key]
    budgets = json.loads(BUDGET_PATH.read_text())
    with pytest.raises((KeyError, ValueError)):
        validate_policy(data, budgets, frozen_tasks())


def test_missing_or_changed_numeric_budget_and_single_agent_contract():
    from evaluation.frozen.freeze import policy

    budgets = json.loads(BUDGET_PATH.read_text())
    data = policy()
    validate_policy(data, budgets, frozen_tasks())
    altered = deepcopy(budgets)
    del altered["categories"]["live-easy"]["limits"]["tokens"]
    with pytest.raises(TypeError):
        validate_policy(data, altered, frozen_tasks())
    altered = deepcopy(budgets)
    altered["total"]["tokens"] = 1
    with pytest.raises(ValueError):
        validate_policy(data, altered, frozen_tasks())
    with pytest.raises(ValueError):
        validate_policy({**data, "roles": ["main", "child"]}, budgets, frozen_tasks())


def test_source_fingerprint_detects_new_deleted_and_modified_python(tmp_path):
    (tmp_path / "src").mkdir()
    original = tmp_path / "src/main.py"
    original.write_text("value=1\n")
    (tmp_path / "pyproject.toml").write_text("identity=1\n")
    (tmp_path / "uv.lock").write_text("identity=1\n")
    before = source_files(tmp_path)
    original.write_text("value=2\n")
    assert source_files(tmp_path) != before
    original.write_text("value=1\n")
    added = tmp_path / "src/extra.py"
    added.write_text("extra=1\n")
    assert source_files(tmp_path) != before
    added.unlink()
    original.unlink()
    assert source_files(tmp_path) != before


def test_external_judge_cli_works_outside_repository(tmp_path):
    import subprocess
    import sys

    from evaluation.specialty.build import initialize

    case = cases()[0]
    base_root = tmp_path / "base"
    base_root.mkdir()
    base = initialize(base_root, case.files)
    data = manifest(case, "unused", base, tmp_path)
    hidden = tmp_path / "acceptance.json"
    hidden.write_text(json.dumps(data))
    patch = tmp_path / "submission.diff"
    patch.write_text("")
    output = tmp_path / "out"
    (output / "worker").mkdir(parents=True)
    (output / "worker/final.txt").write_text(json.dumps(case.answers[0]))
    (output / "worker/worker-result.json").write_text(json.dumps({"final": {"path": "final.txt"}}))
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "evaluation/frozen/judge.py"),
            "--manifest",
            str(hidden),
            str(patch),
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert all(json.loads(result.stdout)["conditions"].values())


@pytest.mark.parametrize("identity", ["FS19", "FS20", "FS21", "FS22", "FS23", "FS24"])
async def test_frozen_business_records_builtin_and_real_mcp(tmp_path, identity):
    from novacode.evaluation.specialty_tools import BUILTIN_RECORDS, MCP_RECORDS

    case = next(c for c in cases() if c.task_id == identity)
    path = tmp_path / "records.json"
    path.write_text(json.dumps(case.records))
    registry = registry_for(identity, case.records, lambda *a, **kw: None)
    for name in BUILTIN_RECORDS.get(identity, []):
        key = next(iter(case.records[name]))
        result = await registry.get(name).execute(json.dumps({"key": key}))
        assert json.loads(result.content) == case.records[name][key]
    manager = await connect_records(identity, path)
    try:
        if manager is not None:
            tools = {t.name(): t for t in manager.tools()}
            assert set(tools) == {"mcp__specialty__" + n for n in MCP_RECORDS[identity]}
            for name in MCP_RECORDS[identity]:
                key = next(iter(case.records[name]))
                result = await tools["mcp__specialty__" + name].execute(json.dumps({"key": key}))
                assert json.loads(result.content) == case.records[name][key]
    finally:
        if manager is not None:
            await manager.close()
            assert all(t.done() for t in manager._tasks)
