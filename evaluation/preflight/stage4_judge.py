"""代表题的外部官方判题进程；只向独立干净容器复制提交和隐藏验收。"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

import docker
import pyarrow.parquet as parquet
import swebench
from swebench.harness.docker_utils import copy_to_container, exec_run_with_timeout
from swebench.harness.grading import get_eval_report
from swebench.harness.test_spec.test_spec import make_test_spec

GRADER_COMMIT = "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"
DATA_SHA = "080e36e46198bf9c177a6b077624d4028baf6ff04d661c332cc1fe1e5dfa50b2"
INSTANCE = "joke2k__faker-2096"
IMAGE = (
    "starryzhang/sweb.eval.x86_64.joke2k_1776_faker-2096@"
    "sha256:d0bedf38180bc7a970a9da180f4cfa234f24db7b1188b278f0d3b3a94b7fadb8"
)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def identity():
    grader = Path(swebench.__file__).resolve().parents[1]
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=grader, text=True).strip()
        == GRADER_COMMIT
    )
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=grader).strip()


def prepare(dataset, output):
    identity()
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == DATA_SHA
    row = next(r for r in parquet.read_table(dataset).to_pylist() if r["instance_id"] == INSTANCE)
    output.mkdir(exist_ok=False)
    public = output / "public"
    hidden = output / "hidden"
    public.mkdir()
    hidden.mkdir()
    save(public / "requests.json", {"schema_version": 1, "requests": [row["problem_statement"]]})
    save(public / "initial.json", {"schema_version": 1, "base_commit": row["base_commit"]})
    save(hidden / "row.json", row)
    (hidden / "judge.py").write_bytes(Path(__file__).read_bytes())
    save(
        hidden / "acceptance.json",
        {
            "command": [sys.executable, str((hidden / "judge.py").resolve())],
            "timeout_seconds": 180,
        },
    )
    print(json.dumps({"base_commit": row["base_commit"], "image": IMAGE, "instance": INSTANCE}))


def grade(patch, output):
    identity()
    row = json.loads(Path(__file__).with_name("row.json").read_text())
    spec = make_test_spec(row, namespace="starryzhang")
    client = docker.from_env()
    container = client.containers.create(
        IMAGE,
        command="tail -f /dev/null",
        detach=True,
        network_mode="none",
        nano_cpus=2_000_000_000,
        mem_limit=4 * 1024**3,
        memswap_limit=4 * 1024**3,
        pids_limit=256,
        labels={"novacode.stage4": "independent-grading"},
    )
    try:
        container.start()
        copy_to_container(container, patch, PurePosixPath("/submission.diff"))
        if patch.stat().st_size:
            applied = container.exec_run(
                ["git", "apply", "--binary", "/submission.diff"], workdir="/testbed"
            )
            assert applied.exit_code == 0, "提交不能在独立 base 应用"
        script = output / "independent-eval.sh"
        script.write_text(spec.eval_script)
        copy_to_container(container, script, PurePosixPath("/eval.sh"))
        logs, timed_out, seconds = exec_run_with_timeout(
            container, "/bin/bash /eval.sh", timeout=120
        )
        assert not timed_out, "独立判题超时"
        log = output / "independent-test-output.txt"
        log.write_text(logs if isinstance(logs, str) else logs.decode("utf-8", errors="replace"))
        official = get_eval_report(
            spec,
            {"instance_id": INSTANCE, "model_patch": patch.read_text()},
            str(log),
            include_tests_status=True,
        )
        save(output / "official-report.json", official)
        resolved = official[INSTANCE]["resolved"]
        print(
            json.dumps(
                {
                    "conditions": {"official-tests": bool(resolved)},
                    "live_resolved": bool(resolved),
                    "official_report_sha256": hashlib.sha256(
                        (output / "official-report.json").read_bytes()
                    ).hexdigest(),
                    "grading_seconds": seconds,
                }
            )
        )
    finally:
        container.remove(force=True)
        client.close()


if __name__ == "__main__":
    if "--prepare" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--prepare", action="store_true")
        parser.add_argument("--dataset", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args()
        prepare(args.dataset, args.output)
    else:
        grade(Path(sys.argv[1]), Path(sys.argv[2]))
