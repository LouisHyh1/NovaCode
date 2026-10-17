"""固定镜像中的离线正负例：提取、归档、恢复、独立判题；零模型调用。"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from evaluation.formal.preservation import archive_run, restore_run
from evaluation.formal.preserve_history import HISTORY, REPO, sha_file
from evaluation.formal.recover_history import docker
from evaluation.formal.run import save
from evaluation.specialty.build import initialize
from novacode.evaluation.artifacts import business_state, capture_baseline, extract_patch


def run_judge(
    command: list[str], patch: Path, output: Path, timeout: int, worker: Path | None = None
) -> dict[str, Any]:
    output.mkdir()
    if worker is not None:
        shutil.copytree(worker, output / "worker")
    result = subprocess.run(
        [*command, str(patch), str(output)],
        capture_output=True,
        timeout=timeout + 30,
        env={**os.environ, "PATH": "/snap/bin:" + os.environ["PATH"]},
    )
    (output / "stdout.txt").write_bytes(result.stdout)
    (output / "stderr.txt").write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(
            f"独立判题失败 exit={result.returncode}: "
            + result.stderr.decode(errors="replace")[-2500:]
        )
    report: dict[str, Any] = json.loads(result.stdout)
    save(output / "report.json", report)
    return report


def prepare_source(
    kind: str,
    assets: Path,
    task_id: str,
    task: dict[str, Any],
    acceptance: dict[str, Any],
    source: Path,
) -> None:
    if kind == "live" and not source.exists():
        environment = json.loads((assets / "public" / task_id / "environment.json").read_text())
        container = "novacode-chain-initial-" + uuid4().hex
        docker(
            "create",
            "--name",
            container,
            "--network",
            "none",
            "--cpus",
            "2",
            "--memory",
            "4g",
            "--entrypoint",
            "tail",
            environment["image"],
            "-f",
            "/dev/null",
        )
        # 从未启动的新容器复制可信初始树；没有历史命令或参考答案进入它。
        try:
            docker("cp", container + ":/testbed", str(source), timeout=240)
        finally:
            docker("rm", container)
    elif kind == "specialty" and not source.exists():
        assert initialize(source, acceptance["initial_files"]) == task["base_commit"]


def preplace(
    workspace: Path,
    task_id: str,
    correct: bool,
    reference: str,
    kind: str,
    acceptance: dict[str, Any],
) -> str | None:
    if correct and reference:
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "apply", "--binary", "-"],
            cwd=workspace,
            input=reference.encode(),
            check=True,
            capture_output=True,
        )
    if correct and kind == "specialty":
        for path, content in acceptance["reference"].items():
            (workspace / path).write_text(content)
    extra = None
    if task_id == "FL07":
        path = workspace / "coverage.xml"
        path.write_bytes(path.read_bytes() + b"\n<!-- evidence chain -->\n")
        extra = "coverage.xml"
    elif task_id == "FL08":
        paths = sorted(workspace.glob(".hypothesis/unicode_data/*/codec-utf-8.json.gz"))
        assert len(paths) == 1
        path = paths[0]
        path.write_bytes(gzip.compress(gzip.decompress(path.read_bytes()), mtime=1))
        extra = str(path.relative_to(workspace))
    elif task_id == "FL09":
        path = workspace / "assets/external/test_assets/custom_script.js"
        assert path.is_symlink()
    return extra


def validate(output: Path, evidence: Path, resume: bool = False) -> None:
    output.mkdir(parents=True, exist_ok=resume)
    evidence.mkdir(parents=True, exist_ok=resume)
    for name in ("sources", "initial-baselines", "runs", "archives", "restored"):
        (output / name).mkdir(exist_ok=resume)
    rows: list[dict[str, Any]] = []
    peak_used = 0
    start_free = shutil.disk_usage(output).free
    for task_id in ("FL20", "FL07", "FL08", "FL09", "FS09"):
        completed = [
            evidence / (task_id + suffix + ".json") for suffix in ("-positive", "-negative")
        ]
        if all(p.exists() for p in completed):
            rows.extend(json.loads(p.read_text()) for p in completed)
            continue
        source = output / "sources" / task_id
        baseline = output / "initial-baselines" / task_id
        kind = "live" if task_id.startswith("FL") else "specialty"
        assets = (
            REPO
            / "evaluation/frozen"
            / ("live-assets" if kind == "live" else "specialty-assets-v2")
        )
        task = json.loads((assets / "contracts" / (task_id + ".json")).read_text())
        acceptance = json.loads((assets / "external" / task_id / "acceptance.json").read_text())
        prepare_source(kind, assets, task_id, task, acceptance, source)
        reference = (
            json.loads((assets / "external" / task_id / "row.json").read_text())["patch"]
            if kind == "live"
            else ""
        )
        info = (
            json.loads((baseline / "manifest.json").read_text())
            if baseline.exists()
            else capture_baseline(source, task["base_commit"], baseline)
        )
        baseline_record = (
            evidence / (task_id + "-baseline.json"),
            {
                "task_id": task_id,
                "base_commit": task["base_commit"],
                "manifest_sha256": sha_file(baseline / "manifest.json"),
                "tree": info["tree"],
                "entries": len(info["entries"]),
                "image": acceptance["image"],
                "state": "captured-before-preplaced-edits",
            },
        )
        if baseline_record[0].exists():
            assert json.loads(baseline_record[0].read_text()) == baseline_record[1]
        else:
            save(*baseline_record)
        for correct in (True, False):
            name = task_id + ("-positive" if correct else "-negative")
            if (evidence / (name + ".json")).exists():
                rows.append(json.loads((evidence / (name + ".json")).read_text()))
                continue
            folder = output / "runs" / name
            attempt = 1
            while folder.exists():
                attempt += 1
                folder = output / "runs" / (name + f"-attempt{attempt}")
            folder.mkdir()
            workspace = folder / "workspace"
            shutil.copytree(source, workspace, symlinks=True)
            shutil.copytree(baseline, folder / "baseline", copy_function=os.link)
            extra = preplace(workspace, task_id, correct, reference, kind, acceptance)
            patch, names = extract_patch(workspace, task["base_commit"], folder / "baseline")
            if extra:
                assert extra in names
            if task_id == "FL09":
                assert "assets/external/test_assets/custom_script.js" not in names
            (folder / "submission.diff").write_text(patch)
            state = business_state(workspace)
            (folder / "worker/artifacts").mkdir(parents=True)
            (folder / "worker/artifacts/final.txt").write_text("离线预置修改，不是 Agent 结果。")
            save(folder / "worker/worker-result.json", {"final": {"path": "artifacts/final.txt"}})
            save(
                folder / "result.json",
                {
                    "new_identity": "offline-chain-20261016-" + name,
                    "task_id": task_id,
                    "preplaced_correct": correct,
                    "model_calls": 0,
                },
            )
            archive = output / "archives" / (folder.name + ".tar.gz")
            archive_run(folder, archive)
            assert not workspace.exists()
            restored = output / "restored" / folder.name
            receipt = json.loads((folder / "archive.json").read_text())
            restore_run(archive, receipt, restored)
            assert business_state(restored / "restored-workspace") == state
            report = run_judge(
                acceptance["command"],
                restored / "submission.diff",
                restored / "independent-judge",
                acceptance["timeout_seconds"],
                restored / "worker" if kind == "specialty" else None,
            )
            passed = all(report["conditions"].values())
            assert passed == correct, (name, report)
            peak_used = max(peak_used, start_free - shutil.disk_usage(output).free)
            record = {
                "case": name,
                "new_identity": "offline-chain-20261016-" + name,
                "expected_pass": correct,
                "actual_pass": passed,
                "conditions": report["conditions"],
                "patch_sha256": sha_file(restored / "submission.diff"),
                "archive_sha256": receipt["sha256"],
                "restored_state_equal": True,
                "baseline_entries": len(info["entries"]),
                "changed_paths": names,
                "model_calls": 0,
                "evidence_root": str(restored.relative_to(REPO)),
            }
            save(evidence / (name + ".json"), record)
            rows.append(record)
            print(json.dumps({"case": name, "passed": passed, "state_equal": True}), flush=True)
    # 第 147 次只读使用保留工作区；新产物和新判题不覆盖旧补充恢复。
    original = HISTORY / "runs/novacode-frozen-20261015-v2-FL20-2-no-compression"
    patch, names = extract_patch(
        original / "workspace",
        "a55db38c061522a2b6397516df1eeedf2e8b4dc5",
        output / "initial-baselines/FL20",
    )
    interrupted = output / "interrupted-derived"
    interrupted.mkdir()
    (interrupted / "submission.diff").write_text(patch)
    acceptance = json.loads(
        (REPO / "evaluation/frozen/live-assets/external/FL20/acceptance.json").read_text()
    )
    report = run_judge(
        acceptance["command"],
        interrupted / "submission.diff",
        interrupted / "independent-judge",
        acceptance["timeout_seconds"],
    )
    interrupted_record = {
        "original_run_id": original.name,
        "original_result_sha256": sha_file(original / "result.json"),
        "original_supplemental_sha256": sha_file(original / "recovery-submission.diff"),
        "new_patch_sha256": sha_file(interrupted / "submission.diff"),
        "new_judgment": report,
        "changed_paths": names,
        "new_identity": "historical-final-tree-recheck-20261016",
        "source": "retained-original-workspace plus trusted-fixed-image baseline",
        "original_score_unchanged": True,
        "not_v3_result": True,
    }
    save(evidence / "interrupted-derived.json", interrupted_record)
    summary = {
        "state": "EVIDENCE_CHAIN_VALID",
        "cases": len(rows),
        "rows": rows,
        "model_calls": 0,
        "scripted_or_preplaced_only": True,
        "peak_observed_disk_growth_bytes": peak_used,
        "free_disk_bytes_after": shutil.disk_usage(output).free,
        "interrupted": interrupted_record,
    }
    sizes = {
        task: sum(
            p.lstat().st_size
            for p in (output / "sources" / task).rglob("*")
            if p.is_file() and not p.is_symlink()
        )
        + sum(
            p.stat().st_size
            for p in (output / "initial-baselines" / task).rglob("*")
            if p.is_file()
        )
        for task in ("FL20", "FL07", "FL08", "FL09", "FS09")
    }
    summary["measured_snapshot_and_baseline_bytes"] = sizes
    summary["conservative_peak_working_bytes"] = max(sizes.values()) * 3
    save(evidence / "validation.json", summary)
    print(
        json.dumps(
            {
                "state": summary["state"],
                "cases": len(rows),
                "model_calls": 0,
                "peak_observed_disk_growth_bytes": peak_used,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    validate(args.output.resolve(), args.evidence.resolve(), args.resume)
