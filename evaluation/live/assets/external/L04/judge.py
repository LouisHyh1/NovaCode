"""外部独立官方判题；只将提交与隐藏测试复制进全新离线容器。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import docker
import swebench
from swebench.harness.docker_utils import copy_to_container, exec_run_with_timeout
from swebench.harness.grading import get_eval_report
from swebench.harness.test_spec.test_spec import make_test_spec

GRADER_COMMIT = "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"


def save(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def identity() -> None:
    root = Path(swebench.__file__).resolve().parents[1]
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        == GRADER_COMMIT
    )
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip()


def execute(container: Any, command: str) -> str:
    result = container.exec_run(["bash", "-lc", command], workdir="/testbed")
    output = result.output.decode("utf-8", errors="replace")
    if result.exit_code:
        raise RuntimeError(f"执行失败 ({result.exit_code}): {command}\n{output}")
    return str(output)


def create(client: Any, image: str, limits: dict[str, Any]) -> Any:
    return client.containers.create(
        image,
        command="tail -f /dev/null",
        detach=True,
        # none 保留正常 localhost/loopback，同时没有外网接口或默认路由。
        network_mode="none",
        nano_cpus=limits["cpus"] * 1_000_000_000,
        mem_limit=limits["memory_bytes"],
        memswap_limit=limits["memory_bytes"],
        pids_limit=limits["pids"],
        labels={"novacode.stage8": "independent-grading"},
    )


def evaluate(
    container: Any, row: dict[str, Any], patch: Path, output: Path, timeout: int
) -> dict[str, Any]:
    spec = make_test_spec(row, namespace="starryzhang")
    if patch.stat().st_size:
        copy_to_container(container, patch, PurePosixPath("/submission.diff"))
        execute(container, "git apply --binary /submission.diff")
    script = output / "eval.sh"
    script.write_text(spec.eval_script)
    copy_to_container(container, script, PurePosixPath("/eval.sh"))
    logs, timed_out, seconds = exec_run_with_timeout(container, "/bin/bash /eval.sh", timeout)
    log = output / "test-output.txt"
    log.write_text(logs if isinstance(logs, str) else logs.decode("utf-8", errors="replace"))
    (output / "resources.txt").write_text(
        execute(
            container,
            "cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max "
            "/sys/fs/cgroup/memory.peak /sys/fs/cgroup/cpu.stat "
            "/sys/fs/cgroup/memory.events; df -B1 /",
        )
    )
    container.reload()
    save(
        output / "execution.json",
        {
            "seconds": seconds,
            "timed_out": timed_out,
            "State": container.attrs["State"],
        },
    )
    assert not timed_out and seconds <= timeout, "官方判题超时"
    assert not container.attrs["State"]["OOMKilled"], "容器 OOM"
    report = get_eval_report(
        spec, {"instance_id": row["instance_id"], "model_patch": patch.read_text()}, str(log), True
    )
    save(output / "official-report.json", report)
    return {
        "official": report[row["instance_id"]],
        "seconds": seconds,
        "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
    }


def grade(manifest_path: Path, patch: Path, output: Path) -> None:
    identity()
    manifest = json.loads(manifest_path.read_text())
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == manifest["judge_sha256"]
    assert manifest["grader_commit"] == GRADER_COMMIT
    row_path = manifest_path.with_name("row.json")
    assert hashlib.sha256(row_path.read_bytes()).hexdigest() == manifest["row_sha256"]
    row = json.loads(row_path.read_text())
    client = docker.from_env()
    container = create(client, manifest["image"], manifest["limits"])
    try:
        container.start()
        assert execute(container, "git rev-parse HEAD").strip() == row["base_commit"]
        result = evaluate(container, row, patch, output, manifest["timeout_seconds"])
        resolved = result["official"]["resolved"]
        print(
            json.dumps(
                {
                    "conditions": {"official-tests": bool(resolved)},
                    "live_resolved": bool(resolved),
                    "grading_seconds": result["seconds"],
                }
            )
        )
    finally:
        container.remove(force=True)
        client.close()


if __name__ == "__main__":
    grade(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
