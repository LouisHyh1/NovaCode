"""冻结专项公开/隐藏包建设和独立离线资格核验，不调用模型。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "evaluation/specialty"))

from evaluation.frozen.definitions import FrozenCase, cases  # noqa: E402
from evaluation.frozen.judge import grade  # noqa: E402
from evaluation.specialty.build import asset, initialize  # noqa: E402
from evaluation.specialty.judge import run_tests, valid_tests  # noqa: E402
from novacode.evaluation.assets import validate_task_assets  # noqa: E402
from novacode.evaluation.contracts import (  # noqa: E402
    Asset,
    EvaluationTask,
    fingerprint,
    json_text,
    require,
)
from novacode.evaluation.corpus import Qualification, SelectionLedger, quota_report  # noqa: E402


def implementation() -> dict[str, str]:
    return {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in (
            "evaluation/frozen/specialty.py",
            "evaluation/frozen/definitions.py",
            "evaluation/frozen/judge.py",
            "evaluation/specialty/judge.py",
            "evaluation/specialty/build.py",
            "evaluation/specialty/test_driver.py",
        )
    }


def manifest(case: FrozenCase, image: str, base: str, hidden: Path) -> dict[str, Any]:
    conditions = ["scope"]
    if case.answers:
        conditions.append("facts")
    if case.checks:
        conditions.append("behavior")
    if case.mutants:
        conditions.extend(["correct-tests", "all-mutants"])
    return {
        "schema_version": 1,
        "task_id": case.task_id,
        "conditions": conditions,
        "initial_files": case.files,
        "reference": case.reference,
        "checks": case.checks,
        "answers": case.answers,
        "mutants": case.mutants,
        "test_target": case.test_target,
        "allowed_paths": case.allowed,
        "base_commit": base,
        "image": image,
        "command": [
            sys.executable,
            str(Path(__file__).with_name("judge.py")),
            "--manifest",
            str(hidden / case.task_id / "acceptance.json"),
        ],
        "timeout_seconds": 180,
        "implementation": implementation(),
    }


def build(output: Path, image: str) -> None:
    output.mkdir(parents=True, exist_ok=False)
    (output / "contracts").mkdir()
    public, hidden = output / "public", output / "external"
    for case in cases():
        with tempfile.TemporaryDirectory(prefix="frozen-base-") as directory:
            base = initialize(Path(directory), case.files)
        initial = asset(
            public,
            case.task_id + "/initial.json",
            {"schema_version": 1, "base_commit": base, "files": case.files},
        )
        request = asset(
            public,
            case.task_id + "/requests.json",
            {"schema_version": 1, "requests": case.requests},
        )
        env = asset(
            public,
            case.task_id + "/environment.json",
            {
                "schema_version": 1,
                "image": image,
                "resources": {"cpus": 1, "memory_bytes": 536870912, "pids": 64},
                "target_python": "python",
                "test_command": "python -m unittest discover -s tests -v",
                "task_id": case.task_id,
                "tool_records": case.records,
                "fault_rule": "first-valid-grep-v1"
                if case.task_id in ("FS09", "FS11")
                else "first-standard-test-timeout-v1"
                if case.task_id in ("FS10", "FS12")
                else None,
                "pressure_dimensions": {
                    "script_requests": len(case.requests),
                    "public_bytes": sum(len(v.encode()) for v in case.files.values()),
                },
            },
        )
        acceptance = manifest(case, image, base, hidden.resolve())
        proof = asset(hidden, case.task_id + "/acceptance.json", acceptance)
        task = EvaluationTask(
            task_id=case.task_id,
            candidate_id=case.family,
            source="specialty",
            suite="frozen",
            family_id="specialty-frozen-v1/" + case.family,
            problem_keys=("defect:" + case.family,),
            repository="specialty-frozen/" + case.family,
            source_revision="specialty-frozen-v1",
            base_commit=base,
            difficulty=case.difficulty,
            category=case.category,
            pressure_tags=tuple(case.pressure),
            budget_category="specialty-" + case.category,
            initial_state=initial,
            public_input=request,
            environment=env,
            acceptance_assets=(proof,),
            acceptance_conditions=tuple(acceptance["conditions"]),
            allowed_paths=tuple(case.allowed),
            builder_id="specialty-builder-v1",
        )
        (output / "contracts" / (case.task_id + ".json")).write_text(json_text(task) + "\n")


def controls(case: FrozenCase, m: dict[str, Any]) -> dict[str, Any]:
    answer = json.dumps(case.answers[0]) if case.answers else ""
    result = {
        "missing-goal": grade(m, {}, "{}"),
        "out-of-scope": grade(m, {**case.reference, "forbidden.py": "changed"}, answer),
    }
    if case.answers:
        result["wrong-facts"] = grade(m, case.reference, '{"reason":"wrong"}')
    if case.mutants:
        for key, code in {
            "collection-error": "import missing_dependency\n",
            "skipped": (
                "import unittest\n@unittest.skip('skip')\nclass "
                "T(unittest.TestCase):\n    def test_skip(self)"
                ": pass\n"
            ),
            "unconditional": (
                "import unittest\nclass T(unittest.TestCase):\n "
                "   def test_failure(self): self.fail('bad')\n"
            ),
        }.items():
            result[key] = grade(m, {"tests/test_added.py": code}, "")
        result["deleted-existing"] = grade(m, {**case.reference, "tests/test_existing.py": ""}, "")
    if case.checks:
        for name in case.reference:
            result["missing-" + name] = grade(
                m, {k: v for k, v in case.reference.items() if k != name}, answer
            )
    return result


def validate(assets: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    tasks = tuple(
        EvaluationTask.from_json(p.read_text())
        for p in sorted((assets / "contracts").glob("*.json"))
    )
    hidden = assets / "external"
    ledger = SelectionLedger(
        output / "selection.jsonl",
        tasks,
        assets / "public",
        hidden,
        frozenset(t.budget_category for t in tasks),
    )
    for task, case in zip(tasks, cases(), strict=True):
        require(task.task_id == case.task_id, "专项身份不一致")
        requests = validate_task_assets(task, assets / "public", hidden, ledger.budget_categories)
        m = json.loads((hidden / task.acceptance_assets[0].path).read_text())
        require(m["implementation"] == implementation(), "专项判题源码变化")
        env = run_tests(
            {
                **{k: v for k, v in case.files.items() if not k.startswith("tests/")},
                "tests/test_env.py": test_environment(),
            },
            m["image"],
        )
        require(
            valid_tests(env) and env["successful"] and bool(env["resource_limits"]), "专项环境无效"
        )
        reference = grade(m, case.reference, json.dumps(case.answers[0]) if case.answers else "")
        require(all(reference["conditions"].values()), f"正确对照失败 {task.task_id}: {reference}")
        negative = controls(case, m)
        require(all(not all(r["conditions"].values()) for r in negative.values()), "负例误判成功")
        evidence = asset(
            hidden,
            task.task_id + "/qualification-checks.json",
            {
                "schema_version": 1,
                "task_sha256": fingerprint(task),
                "reference": reference,
                "environment": env,
                "negatives": negative,
                "family": case.family,
                "script_requests": len(requests),
                "model_calls": 0,
            },
        )
        qualification = Qualification(
            task_sha256=fingerprint(task),
            environment_verified=True,
            resources_verified=True,
            original_target_failed=False,
            original_regressions_passed=False,
            reference_passes=(),
            correct_implementation_passed=True,
            negative_controls_passed=True,
            evidence=(evidence,),
        )
        path = hidden / case.task_id / "qualification.json"
        path.write_text(json_text(qualification) + "\n")
        proof = Asset(
            task.task_id + "/qualification.json", hashlib.sha256(path.read_bytes()).hexdigest()
        )
        ledger.append(task.task_id, "SELECT", "selection-rule", "独立业务事实与缺陷家族")
        ledger.append(
            task.task_id, "ADMIT", "qualification-passed", "离线容器正确与负例", evidence=proof
        )
        print(task.task_id, "ADMITTED", flush=True)
    states, _ = ledger.replay()
    report = quota_report(tasks, states)
    row = next(r for r in report["rows"] if r["suite"] == "frozen" and r["source"] == "specialty")
    require(
        row["gap"] == 0
        and not any(row["difficulty_gaps"].values())
        and not any(row["category_gaps"].values()),
        "配额缺失",
    )
    (output / "admitted.json").write_text(
        json.dumps(
            {"schema_version": 1, "state": "ADMITTED", "quota": report, "model_calls": 0}, indent=2
        )
        + "\n"
    )


def test_environment() -> str:
    return (
        "import unittest\nfrom pathlib import Path\nclas"
        "s T(unittest.TestCase):\n    def test_source(s"
        "elf):\n        for p in Path('.').glob('*.py')"
        ": compile(p.read_text(), str(p), 'exec')\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "validate"))
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--image")
    args = parser.parse_args()
    if args.mode == "build":
        build(args.assets.resolve(), args.image)
    else:
        validate(args.assets.resolve(), args.output.resolve())
