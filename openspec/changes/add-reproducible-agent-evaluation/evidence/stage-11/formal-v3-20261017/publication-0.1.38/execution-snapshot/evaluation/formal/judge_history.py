"""仅对新增恢复补丁执行独立历史复核；绝不改写原评分。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from evaluation.formal.preserve_history import HISTORY, REPO, sha_file
from evaluation.formal.run import save


def judge(output: Path) -> None:
    destination = output / "derived-judging"
    destination.mkdir(exist_ok=False)
    summary = json.loads((output / "recovery-summary-v3.json").read_text())
    selected = []
    for row in summary["rows"]:
        folders = [
            p
            for p in output.glob("recoveries*/" + row["run_id"])
            if (p / "recovery.json").exists()
            and sha_file(p / "recovery.json") == row["receipt_sha256"]
        ]
        assert len(folders) == 1
        selected.append((row["run_id"], folders[0] / "candidate.diff", row["classification"]))
    interrupted = "novacode-frozen-20261015-v2-FL20-2-no-compression"
    selected.append(
        (
            interrupted,
            HISTORY / "runs" / interrupted / "recovery-submission.diff",
            "original-supplemental-patch",
        )
    )
    results = []
    for run_id, patch, classification in selected:
        folder = destination / run_id
        folder.mkdir()
        if not patch.exists():
            save(
                folder / "derived.json",
                {
                    "run_id": run_id,
                    "new_judgment": None,
                    "reason": "局部恢复没有完整候选补丁",
                    "classification": classification,
                },
            )
            continue
        task_id = json.loads((HISTORY / "runs" / run_id / "result.json").read_text())["task_id"]
        acceptance = REPO / "evaluation/frozen/live-assets/external" / task_id / "acceptance.json"
        manifest = json.loads(acceptance.read_text())
        result = subprocess.run(
            [*manifest["command"], str(patch), str(folder)],
            capture_output=True,
            timeout=manifest["timeout_seconds"] + 30,
            env={**os.environ, "PATH": "/snap/bin:" + os.environ["PATH"]},
        )
        (folder / "stdout.txt").write_bytes(result.stdout)
        (folder / "stderr.txt").write_bytes(result.stderr)
        data = {
            "run_id": run_id,
            "new_identity": "historical-recheck-20261016-" + run_id,
            "source_patch": str(patch.relative_to(REPO)),
            "source_sha256": sha_file(patch),
            "classification": classification,
            "new_judgment": json.loads(result.stdout) if result.returncode == 0 else None,
            "exit_code": result.returncode,
            "diagnostic_stderr": result.stderr.decode(errors="replace")[-6000:],
            "original_unchanged": True,
            "model_calls": 0,
            "not_v3_result": True,
        }
        save(folder / "derived.json", data)
        results.append(data)
        print(json.dumps({k: data[k] for k in ("run_id", "new_judgment", "exit_code")}), flush=True)
    save(
        output / "derived-judging-summary.json",
        {
            "schema_version": 1,
            "rows": results,
            "model_calls": 0,
            "does_not_prove_historical_byte_completeness": True,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    judge(args.output.resolve())
