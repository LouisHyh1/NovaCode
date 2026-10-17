"""专项入库审计：全题容器正确对照、负例、指纹和追加选择账本。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build import asset
from definitions import cases
from judge import grade, run_tests, valid_tests

from novacode.evaluation.assets import validate_task_assets
from novacode.evaluation.contracts import EvaluationTask, fingerprint, json_text, require
from novacode.evaluation.corpus import Qualification, SelectionLedger, quota_report


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
        frozenset(task.budget_category for task in tasks),
    )
    summaries = []
    for task, case in zip(tasks, cases(), strict=True):
        require(task.task_id == case.task_id, "候选身份不一致")
        requests = validate_task_assets(task, assets / "public", hidden, ledger.budget_categories)
        manifest = json.loads((hidden / task.acceptance_assets[0].path).read_text())
        environment = run_tests(
            {
                **{
                    name: value
                    for name, value in case.files.items()
                    if not name.startswith("tests/")
                },
                "tests/test_environment.py": (
                    "import unittest\nfrom pathlib import Path\n"
                    "class Environment(unittest.TestCase):\n"
                    "    def test_source(self):\n"
                    "        for path in Path('.').glob('*.py'):\n"
                    "            compile(path.read_text(), str(path), 'exec')\n"
                ),
            },
            manifest["image"],
        )
        require(
            valid_tests(environment)
            and environment["successful"]
            and bool(environment.get("resource_limits")),
            "专项环境/资源未核验",
        )
        answer = json.dumps(case.answers[0]) if case.answers else ""
        reference = grade(manifest, case.reference, answer)
        require(all(reference["conditions"].values()), f"正确对照失败 {task.task_id}: {reference}")
        negatives = {
            "missing-goal": grade(manifest, {}, "{}"),
            "out-of-scope": grade(manifest, {**case.reference, "forbidden.py": "changed"}, answer),
        }
        if case.answers:
            for index, option in enumerate(case.answers):
                require(
                    all(grade(manifest, case.reference, json.dumps(option))["conditions"].values()),
                    "合法替代答案被拒绝",
                )
            negatives["wrong-facts"] = grade(manifest, case.reference, '{"reason":"wrong"}')
        if case.mutants:
            controls = {
                "collection-error": "import missing_dependency\n",
                "skipped": "import unittest\n@unittest.skip('skip')\nclass T(unittest.TestCase):\n"
                "    def test_skip(self): pass\n",
                "unconditional": "import unittest\nclass T(unittest.TestCase):\n"
                "    def test_fail(self): self.fail('wrong')\n",
                "deleted-existing": "",
            }
            for key, test in controls.items():
                changes = (
                    {"tests/test_added.py": test}
                    if key != "deleted-existing"
                    else {
                        **case.reference,
                        "tests/test_existing.py": "",
                    }
                )
                negatives[key] = grade(manifest, changes, "")
        if task.task_id == "S09":
            for path in ("config.py", "client.py"):
                negatives["erased-" + path] = grade(
                    manifest, {**case.reference, path: case.files[path]}, ""
                )
        require(
            all(not all(result["conditions"].values()) for result in negatives.values()),
            f"负例被误判通过: {task.task_id}",
        )
        evidence = asset(
            hidden,
            f"{task.task_id}/qualification-checks-{output.name}.json",
            {
                "schema_version": 1,
                "task_sha256": fingerprint(task),
                "reference": reference,
                "environment": environment,
                "negatives": negatives,
                "script_requests": len(requests),
                "input_source": "fixed-public-script",
                "model_runs": 0,
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
        qualification_path = hidden / f"{task.task_id}/qualification-{output.name}.json"
        qualification_path.write_text(json_text(qualification) + "\n")
        from novacode.evaluation.contracts import Asset

        proof = Asset(
            qualification_path.relative_to(hidden).as_posix(),
            hashlib.sha256(qualification_path.read_bytes()).hexdigest(),
        )
        ledger.append(task.task_id, "SELECT", "selection-rule", "事前专项类别与难度合同")
        ledger.append(
            task.task_id,
            "ADMIT",
            "qualification-passed",
            "独立离线容器正确/负例验收",
            evidence=proof,
        )
        summaries.append(
            {
                "task_id": task.task_id,
                "task_sha256": fingerprint(task),
                "initial_sha256": task.initial_state.sha256,
                "qualification": proof.path,
                "negative_controls": list(negatives),
            }
        )
        print(task.task_id, "ADMITTED", flush=True)
    states, _ = ledger.replay()
    report = quota_report(tasks, states)
    row = next(
        row
        for row in report["rows"]
        if row["suite"] == "development" and row["source"] == "specialty"
    )
    require(
        row["gap"] == 0
        and all(v == 0 for v in row["difficulty_gaps"].values())
        and all(v == 0 for v in row["category_gaps"].values()),
        "专项配额不满足",
    )
    (output / "admitted.json").write_text(
        json.dumps(
            {"schema_version": 1, "tasks": summaries, "quota": report}, ensure_ascii=False, indent=2
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validate(args.assets, args.output)
