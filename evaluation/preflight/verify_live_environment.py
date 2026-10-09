"""第一阶段离线核验：复用官方判题，禁止模型调用，输出写到外部证据目录。"""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import docker
import pyarrow.parquet as parquet
import swebench
from swebench.harness.docker_utils import copy_to_container, exec_run_with_timeout
from swebench.harness.grading import get_eval_report
from swebench.harness.test_spec.test_spec import make_test_spec

DATA_SHA256 = "080e36e46198bf9c177a6b077624d4028baf6ff04d661c332cc1fe1e5dfa50b2"
GRADER_COMMIT = "ad79b850f15e33992e96f03f6e97f05ddf9aa0be"
IMAGE_SHA256 = "sha256:d0bedf38180bc7a970a9da180f4cfa234f24db7b1188b278f0d3b3a94b7fadb8"
TARGET_IDENTITY = (
    "command -v python; python --version; python -m pip freeze; "
    "git rev-parse HEAD; git status --porcelain"
)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute(container, command):
    result = container.exec_run(["bash", "-lc", command], workdir="/testbed")
    text = result.output.decode("utf-8", errors="replace")
    if result.exit_code:
        raise RuntimeError(f"命令失败 ({result.exit_code}): {command}\n{text}")
    return text


def verify(args):
    grader_root = Path(swebench.__file__).resolve().parents[1]
    grader_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=grader_root, text=True
    ).strip()
    assert grader_commit == GRADER_COMMIT, "官方判题代码不是固定 commit"
    assert not subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=grader_root, text=True
    ).strip(), "官方判题代码有未记录修改"
    data_digest = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    assert data_digest == DATA_SHA256, "数据文件不完整或不是固定 revision"
    rows = parquet.read_table(args.dataset).to_pylist()
    row = next(row for row in rows if row["instance_id"] == args.instance_id)
    spec = make_test_spec(row, namespace="starryzhang")
    args.output.mkdir(parents=True, exist_ok=False)
    client = docker.from_env()
    image = client.images.get(spec.instance_image_key)
    assert image.attrs["RepoDigests"], "镜像缺少 registry digest"
    image_digest = next(
        digest
        for digest in image.attrs["RepoDigests"]
        if digest.split("@")[0] == spec.instance_image_key.split(":")[0]
    )
    assert image_digest.split("@")[1] == IMAGE_SHA256, "镜像移动标签已经变化"
    manifest = {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "grader_commit": grader_commit,
        "instance_id": args.instance_id,
        "base_commit": row["base_commit"],
        "dataset_revision": "b51a86422e10cfd403beb4773e5a2947953e36ec",
        "dataset_sha256": data_digest,
        "verified_pool_size": len(rows),
        "row_sha256": hashlib.sha256(
            json.dumps(row, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "reference_patch_sha256": hashlib.sha256(row["patch"].encode()).hexdigest(),
        "eval_script_sha256": hashlib.sha256(spec.eval_script.encode()).hexdigest(),
        "image_digest": image_digest,
        "image_id": image.id,
        "limits": {"cpu": 2, "memory_bytes": 4 * 1024**3, "swap_bytes": 0, "pids": 256},
        "disk": {
            "shared_filesystem_bytes": list(shutil.disk_usage(args.output)),
            "per_container_quota": None,
        },
        "model_calls_started": 0,
        "env_ready": False,
        "runs": [],
    }
    save(args.output / "manifest.json", manifest)
    try:
        labels = ["original", "reference-1", "reference-2", "reference-3"]
        if args.agent_archive:
            labels.append("reference-with-agent")
        for label in labels:
            directory = args.output / label
            directory.mkdir()
            container = client.containers.create(
                image_digest,
                command="tail -f /dev/null",
                detach=True,
                network_disabled=True,
                nano_cpus=2_000_000_000,
                mem_limit=4 * 1024**3,
                memswap_limit=4 * 1024**3,
                pids_limit=256,
                labels={"novacode.preflight": label},
            )
            try:
                container.start()
                identity_before = execute(container, TARGET_IDENTITY)
                (directory / "target-before.txt").write_text(identity_before)
                assert execute(container, "git rev-parse HEAD").strip() == row["base_commit"]
                assert execute(container, "git status --porcelain").strip() == ""
                if label == "reference-with-agent":
                    with args.agent_archive.open("rb") as archive:
                        assert container.put_archive("/", archive)
                    install = execute(
                        container,
                        "/opt/novacode/uv venv /opt/novacode/venv "
                        "--python /opt/novacode/python/bin/python3.12 && "
                        "/opt/novacode/uv pip install --offline --no-index "
                        "--find-links /opt/novacode/wheels "
                        "--python /opt/novacode/venv/bin/python novacode",
                    )
                    (directory / "agent-install.txt").write_text(install)
                    smoke = execute(
                        container, "/opt/novacode/venv/bin/python /opt/novacode/tool-smoke.py"
                    )
                    (directory / "agent-smoke.txt").write_text(smoke)
                    identity_after = execute(container, TARGET_IDENTITY)
                    (directory / "target-after.txt").write_text(identity_after)
                    assert identity_before == identity_after, "Agent 安装或工具检查改变了目标环境"
                if label != "original":
                    patch = directory / "reference.diff"
                    patch.write_text(row["patch"])
                    copy_to_container(container, patch, PurePosixPath("/reference.diff"))
                    execute(container, "git apply /reference.diff")
                script = directory / "eval.sh"
                script.write_text(spec.eval_script)
                copy_to_container(container, script, PurePosixPath("/eval.sh"))
                started = time.monotonic()
                output, timed_out, runtime = exec_run_with_timeout(
                    container, "/bin/bash /eval.sh", 600
                )
                log = directory / "test-output.txt"
                log.write_text(output)
                assert not timed_out, "判题超时"
                report = get_eval_report(
                    spec,
                    {
                        "instance_id": args.instance_id,
                        "model_patch": "" if label == "original" else row["patch"],
                    },
                    str(log),
                    True,
                )
                save(directory / "report.json", report)
                resources = execute(
                    container,
                    "cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max "
                    "/sys/fs/cgroup/memory.swap.max /sys/fs/cgroup/memory.peak "
                    "/sys/fs/cgroup/cpu.stat /sys/fs/cgroup/memory.events; df -B1 /",
                )
                (directory / "resources.txt").write_text(resources)
                container.reload()
                assert not container.attrs["State"]["OOMKilled"]
                result = report[args.instance_id]
                assert result["patch_successfully_applied"], "官方判题日志未有效解析"
                assert result["resolved"] == (label != "original"), report
                tests = result["tests_status"]
                assert not tests["PASS_TO_PASS"]["failure"], "相关回归失败"
                if label == "original":
                    assert tests["FAIL_TO_PASS"]["failure"], "原始代码没有目标失败"
                manifest["runs"].append(
                    {
                        "label": label,
                        "resolved": result["resolved"],
                        "runtime_seconds": runtime,
                        "wall_seconds": time.monotonic() - started,
                        "report_sha256": hashlib.sha256(
                            (directory / "report.json").read_bytes()
                        ).hexdigest(),
                        "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                    }
                )
                save(args.output / "manifest.json", manifest)
                print(f"{label}: resolved={result['resolved']}, runtime={runtime:.2f}s", flush=True)
            finally:
                container.remove(force=True)
        manifest["env_ready"] = bool(args.agent_archive)
        save(args.output / "manifest.json", manifest)
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--instance-id", default="joke2k__faker-2096")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agent-archive", type=Path)
    verify(parser.parse_args())
