"""开发期验收器修订后的统一复评；原判题与真实轨迹不覆盖。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from novacode.evaluation.contracts import Asset, EvaluationTask, fingerprint, json_text, require

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evaluation.pilot.run import REPO, digest, save  # noqa: E402
from evaluation.specialty.definitions import cases  # noqa: E402


def refresh_contracts() -> None:
    """公开输入不变；只刷新外部验收实现及指纹，由新资格账本重新入库。"""
    assets = REPO / "evaluation/specialty/assets"
    definitions = {case.task_id: case for case in cases()}
    for p in sorted((assets / "contracts").glob("*.json")):
        task = EvaluationTask.from_json(p.read_text())
        acceptance = assets / "external" / task.acceptance_assets[0].path
        manifest = json.loads(acceptance.read_text())
        case = definitions[task.task_id]
        require(
            manifest["initial_files"] == case.files
            and manifest["reference"] == case.reference
            and manifest["mutants"] == case.mutants
            and manifest["checks"] == case.checks,
            "本轮只修订事实验收；初始代码/行为/缺陷不得改变",
        )
        manifest["answers"] = case.answers
        manifest["implementation"] = {
            name: digest(REPO / "evaluation/specialty" / name)
            for name in manifest["implementation"]
        }
        acceptance.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        updated = replace(
            task, acceptance_assets=(Asset(task.acceptance_assets[0].path, digest(acceptance)),)
        )
        p.write_text(json_text(updated) + "\n")


def regrade(root: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    originals = {}
    with tarfile.open(root / "assets-before-calibration.tar.gz") as archive:
        for member in archive.getmembers():
            if member.name.startswith("evaluation/specialty/assets/contracts/") and member.isfile():
                stream = archive.extractfile(member)
                require(stream is not None, "原合同缺失")
                assert stream is not None
                original = EvaluationTask.from_json(stream.read().decode())
                originals[original.task_id] = original
    reports = []
    for path in sorted((root / "runs").glob("*/result.json")):
        old = json.loads(path.read_text())
        if not old["task_id"].startswith("S"):
            continue
        run = path.parent
        assets = REPO / "evaluation/specialty/assets"
        task = EvaluationTask.from_json(
            (assets / "contracts" / (old["task_id"] + ".json")).read_text()
        )
        original = originals[task.task_id]
        require(
            fingerprint(original) == old["task_sha256"]
            and replace(original, acceptance_assets=task.acceptance_assets) == task,
            "除外部验收资产外的合同变化禁止无模型复评",
        )
        manifest_path = assets / "external" / task.acceptance_assets[0].path
        manifest = json.loads(manifest_path.read_text())
        proc = subprocess.run(
            [*manifest["command"], str(run / "submission.diff"), str(run)],
            cwd=REPO,
            capture_output=True,
            timeout=manifest["timeout_seconds"],
            check=True,
        )
        report: dict[str, Any] = json.loads(proc.stdout)
        strict = (
            old["termination"] == "completed"
            and old["cleanup"] == old["scope"] == "passed"
            and all(report["conditions"].values())
        )
        reports.append(
            {
                "run_id": old["run_id"],
                "task_id": task.task_id,
                "old_task_sha256": old["task_sha256"],
                "new_task_sha256": fingerprint(task),
                "original_acceptance": old["acceptance"],
                "grading": report,
                "strict_success_regraded": strict,
                "original_result_sha256": digest(path),
                "ledger_sha256": digest(run / "worker/ledger.jsonl"),
                "new_model_calls": 0,
            }
        )
        save(output / (old["run_id"] + ".json"), reports[-1])
        print(old["run_id"], strict, flush=True)
    save(
        output / "review.json",
        {
            "schema_version": 1,
            "scope": "all_started_specialty_runs",
            "new_model_calls": 0,
            "runs": reports,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["refresh", "regrade"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "refresh":
        refresh_contracts()
    else:
        regrade(args.root.resolve(), args.output.resolve())
