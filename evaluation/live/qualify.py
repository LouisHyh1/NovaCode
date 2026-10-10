"""逐题入库核验；原始失败和三次参考成功与 Agent 环境检查分别留证。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import docker
import pyarrow.parquet as parquet
from judge import create, evaluate, execute, identity, save
from swebench.harness.test_spec.test_spec import make_test_spec

TARGET_IDENTITY = (
    "command -v python; python --version; python -m pip freeze; "
    "git rev-parse HEAD; git status --porcelain"
)


def qualify(
    dataset: Path,
    instance: str,
    output: Path,
    archive: Path,
    rule_path: Path,
    image_transport: str = "docker",
) -> None:
    identity()
    rule = json.loads(rule_path.read_text())
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == rule["dataset_sha256"]
    matches = [r for r in parquet.read_table(dataset).to_pylist() if r["instance_id"] == instance]
    assert matches and len({json.dumps(r, sort_keys=True, default=str) for r in matches}) == 1
    row = matches[0]
    output.mkdir(parents=True, exist_ok=False)
    save(output / "row.json", row)
    result: dict[str, Any] = {
        "schema_version": 1,
        "instance_id": instance,
        "qualified": False,
        "runs": [],
        "model_calls": 0,
        "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "image_transport": image_transport,
    }
    client = docker.from_env()
    try:
        assert shutil.disk_usage(output).free >= rule["limits"]["minimum_free_disk_bytes"]
        key = make_test_spec(row, namespace="starryzhang").instance_image_key
        try:
            image = client.images.get(key)
        except docker.errors.ImageNotFound:
            print("pull", key, flush=True)
            with (output / "image-pull.txt").open("x") as log:
                download = ["/snap/bin/docker", "pull", key]
                if image_transport == "verified-oci":
                    download = [
                        sys.executable,
                        str(Path(__file__).with_name("fetch_image.py")),
                        "--image",
                        key,
                        "--cache",
                        str(Path.home() / ".cache/novacode-evaluation/stage8/oci"),
                    ]
                pull = subprocess.run(
                    download,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=rule["limits"]["image_pull_timeout_seconds"],
                )
            assert pull.returncode == 0, "官方镜像不可用"
            image = client.images.get(key)
        image_digest = next(
            d for d in image.attrs["RepoDigests"] if d.split("@")[0] == key.split(":")[0]
        )
        result.update(
            image=image_digest,
            image_id=image.id,
            base_commit=row["base_commit"],
            dataset_sha256=rule["dataset_sha256"],
            grader_commit=rule["grader_commit"],
        )
        for label in ("original", "reference-1", "reference-2", "reference-3"):
            directory = output / label
            directory.mkdir()
            container = create(client, image_digest, rule["limits"])
            try:
                container.start()
                actual_head = execute(container, "git rev-parse HEAD").strip()
                actual_status = execute(container, "git status --porcelain")
                save(
                    directory / "initial-image-state.json",
                    {
                        "expected_base": row["base_commit"],
                        "actual_head": actual_head,
                        "status": actual_status,
                    },
                )
                assert actual_head == row["base_commit"], "镜像 base_commit 不符"
                assert not actual_status.strip(), "镜像初始工作树不干净"
                before = execute(container, TARGET_IDENTITY)
                (directory / "target-before.txt").write_text(before)
                if label == "reference-3":
                    # 只部署无逐题内容的运行时；隐藏 row、补丁和 eval.sh 尚未复制。
                    assert execute(
                        container,
                        "test ! -e /reference.diff && test ! -e /submission.diff && "
                        "test ! -e /eval.sh && test ! -e /var/run/docker.sock && "
                        "cat /proc/net/route",
                    ).strip()
                    with archive.open("rb") as stream:
                        assert container.put_archive("/", stream)
                    install = execute(
                        container,
                        "/opt/novacode/uv venv /opt/novacode/venv "
                        "--python /opt/novacode/python/bin/python3.12 && "
                        "/opt/novacode/uv pip install --offline --no-index "
                        "--find-links /opt/novacode/wheels "
                        "--python /opt/novacode/venv/bin/python novacode",
                    )
                    (directory / "agent-install.txt").write_text(install)
                    (directory / "agent-smoke.txt").write_text(
                        execute(
                            container, "/opt/novacode/venv/bin/python /opt/novacode/tool-smoke.py"
                        )
                    )
                    after = execute(container, TARGET_IDENTITY)
                    (directory / "target-after.txt").write_text(after)
                    assert before == after, "独立 Agent 环境改变了目标依赖/源码"
                patch = directory / "candidate.diff"
                patch.write_text("" if label == "original" else row["patch"])
                graded = evaluate(
                    container, row, patch, directory, rule["limits"]["test_timeout_seconds"]
                )
                official = graded["official"]
                save(
                    directory / "container.json",
                    {
                        "image": image_digest,
                        "HostConfig": container.attrs["HostConfig"],
                        "State": container.attrs["State"],
                    },
                )
                assert official["patch_successfully_applied"], "官方日志无法有效解析"
                assert not official["tests_status"]["PASS_TO_PASS"]["failure"], "原有回归失败"
                assert official["resolved"] == (label != "original"), "目标验收结果错误"
                if label == "original":
                    assert official["tests_status"]["FAIL_TO_PASS"]["failure"], "原始问题没有失败"
                result["runs"].append({"label": label, **graded})
                print(
                    instance, label, official["resolved"], round(graded["seconds"], 2), flush=True
                )
            finally:
                container.remove(force=True)
        result["qualified"] = True
    except (
        AssertionError,
        RuntimeError,
        StopIteration,
        subprocess.SubprocessError,
        docker.errors.DockerException,
    ) as error:
        result["error"] = type(error).__name__ + ": " + str(error)
        print(instance, "REJECTED", result["error"][:350], flush=True)
    finally:
        save(output / "qualification.json", result)
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--image-transport", choices=("docker", "verified-oci"), default="docker")
    parser.add_argument(
        "--rule", type=Path, default=Path(__file__).with_name("selection-rule.json")
    )
    args = parser.parse_args()
    qualify(args.dataset, args.instance, args.output, args.archive, args.rule, args.image_transport)
