"""实测最大代表树的一次完整归档/恢复峰值，不调用模型或改变任务。"""

from __future__ import annotations

import argparse
import json
import shutil
import threading
import time
from pathlib import Path

from evaluation.formal.preservation import archive_run, restore_run
from evaluation.formal.run import save
from novacode.evaluation.artifacts import extract_patch


def measure(source: Path, baseline: Path, output: Path, evidence: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    free_before = shutil.disk_usage(output).free
    samples = [(time.monotonic(), free_before)]
    stop = threading.Event()

    def monitor() -> None:
        while not stop.wait(0.05):
            free = shutil.disk_usage(output).free
            if free < samples[-1][1]:
                samples.append((time.monotonic(), free))

    observer = threading.Thread(target=monitor, daemon=True)
    observer.start()
    state = "FAILED"
    try:
        folder = output / "run"
        folder.mkdir()
        (output / "archives").mkdir()
        shutil.copytree(source, folder / "workspace", symlinks=True)
        shutil.copytree(baseline, folder / "baseline")
        info = json.loads((baseline / "manifest.json").read_text())
        patch = extract_patch(folder / "workspace", info["base_commit"], folder / "baseline")[0]
        (folder / "submission.diff").write_text(patch)
        archive = output / "archives/run.tar.gz"
        archive_run(folder, archive)
        receipt = json.loads((folder / "archive.json").read_text())
        restore_run(archive, receipt, output / "restored")
        state = "STORAGE_MEASURED"
    finally:
        stop.set()
        observer.join()
        save(
            evidence,
            {
                "state": state,
                "method": "filesystem-free-space-low-watermark-50ms",
                "source": str(source),
                "baseline": str(baseline),
                "free_before_bytes": free_before,
                "minimum_free_bytes_observed": samples[-1][1],
                "peak_increment_bytes": free_before - samples[-1][1],
                "low_watermarks": samples,
                "model_calls": 0,
                "concurrent_host_work_may_increase_estimate": True,
            },
        )
    print("STORAGE_MEASURED", free_before - samples[-1][1], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    measure(
        args.source.resolve(),
        args.baseline.resolve(),
        args.output.resolve(),
        args.evidence.resolve(),
    )
