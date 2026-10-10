"""外部判题：只在全新离线容器复制候选代码/测试，不暴露合同和缺陷目录。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import posixpath
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from build import initialize, write_files

from novacode.evaluation.artifacts import extract_patch, scope_status
from novacode.evaluation.contracts import require


def answer_json(answer: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    matches = []
    for index, char in enumerate(answer):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(answer[index:])
            if isinstance(value, dict):
                matches.append(value)
        except ValueError:
            continue
    # 拒绝多个互相冲突的完整候选答案；自由解释仍可保留。
    return matches[0] if len(matches) == 1 else None


def canonical(value: dict[str, Any]) -> dict[str, Any]:
    if not {"files", "symbols", "relations"} <= value.keys():
        return value
    result = dict(value)
    result["files"] = sorted(posixpath.normpath(p.replace("\\", "/")) for p in value["files"])
    result["symbols"] = sorted(s.replace("./", "", 1) for s in value["symbols"])
    result["relations"] = sorted(value["relations"])
    return result


def facts_pass(answer: str, expected: list[dict[str, Any]]) -> bool:
    actual = answer_json(answer)
    if actual is None:
        return False
    try:
        return any(canonical(actual) == canonical(option) for option in expected)
    except (TypeError, AttributeError, ValueError):
        return False


def run_tests(files: dict[str, str], image: str, *, local: bool = False) -> dict[str, Any]:
    driver = Path(__file__).with_name("test_driver.py").read_text()
    if local:
        # 仅用于受信正确/负例的快速机制检查；候选提交必须走容器。
        import sys

        with tempfile.TemporaryDirectory(prefix="specialty-check-") as name:
            root = Path(name)
            write_files(root, {**files, "test_driver.py": driver})
            proc = subprocess.run(
                [sys.executable, "test_driver.py"], cwd=root, capture_output=True, timeout=15
            )
            path = root / "result.json"
            return (
                json.loads(path.read_text())
                if path.exists()
                else {
                    "run": 0,
                    "errors": 1,
                    "skipped": 0,
                    "failures": 0,
                    "successful": False,
                    "called": [],
                    "exit": proc.returncode,
                }
            )
    name = "specialty-judge-" + uuid4().hex
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for filename, content in {**files, "test_driver.py": driver}.items():
            raw = content.encode()
            entry = tarfile.TarInfo("testbed/" + filename)
            entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))
    try:
        subprocess.run(
            [
                "docker",
                "create",
                "--name",
                name,
                "--network",
                "none",
                "--cpus",
                "1",
                "--memory",
                "512m",
                "--memory-swap",
                "512m",
                "--pids-limit",
                "64",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                image,
                "sh",
                "-c",
                "mkdir /grade && cd /grade && tar -xf /input.tar --strip-components=1 "
                "&& python test_driver.py --container",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        limits = json.loads(
            subprocess.check_output(["docker", "inspect", "--format", "{{json .HostConfig}}", name])
        )
        require(
            limits["Memory"] == 536870912
            and limits["NanoCpus"] == 1_000_000_000
            and limits["PidsLimit"] == 64
            and limits["NetworkMode"] == "none",
            "判题隔离/资源限制不符",
        )
        subprocess.run(
            ["docker", "cp", "-", name + ":/"],
            input=tar_one(data.getvalue()),
            check=True,
            capture_output=True,
            timeout=30,
        )
        proc = subprocess.run(["docker", "start", "-a", name], capture_output=True, timeout=20)
        copy = subprocess.run(
            ["docker", "cp", name + ":/tmp/specialty-result.json", "-"],
            capture_output=True,
            timeout=10,
        )
        with tarfile.open(fileobj=io.BytesIO(copy.stdout)) as archive:
            if copy.returncode or proc.returncode:
                return {
                    "run": 0,
                    "errors": 1,
                    "skipped": 0,
                    "failures": 0,
                    "successful": False,
                    "called": [],
                    "stdout": proc.stdout.decode(errors="replace"),
                    "stderr": proc.stderr.decode(errors="replace"),
                    "copy_error": copy.stderr.decode(errors="replace"),
                }
            stream = archive.extractfile("specialty-result.json")
            require(stream is not None, "缺少判题结果")
            assert stream is not None
            result = dict(json.load(stream))
            result["resource_limits"] = {
                key: limits[key]
                for key in ("Memory", "MemorySwap", "NanoCpus", "PidsLimit", "NetworkMode")
            }
            return result
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=True, capture_output=True, timeout=15)


def tar_one(raw: bytes) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        entry = tarfile.TarInfo("input.tar")
        entry.size = len(raw)
        archive.addfile(entry, io.BytesIO(raw))
    return data.getvalue()


def valid_tests(report: dict[str, Any]) -> bool:
    return bool(report["run"] > 0 and report["errors"] == report["skipped"] == 0)


def grade(
    manifest: dict[str, Any], changes: dict[str, str], answer: str, *, local: bool = False
) -> dict[str, Any]:
    initial = manifest["initial_files"]
    names = [name for name, value in changes.items() if initial.get(name) != value]
    scope = scope_status(names, tuple(manifest["allowed_paths"])) == "passed"
    conditions: dict[str, bool] = {"scope": scope}
    details: dict[str, Any] = {}
    if manifest["answers"]:
        conditions["facts"] = facts_pass(answer, manifest["answers"])
    if manifest["mutants"]:
        added = changes.get("tests/test_added.py", "")
        files = {**initial, "tests/test_added.py": added}
        correct = run_tests(files, manifest["image"], local=local)
        # 仅现有测试通过、没有新增行为执行不能冒充有效补测。
        production = "settings.py" if manifest["task_id"] == "S03" else "operation.py"
        conditions["correct-tests"] = bool(added) and valid_tests(correct) and correct["successful"]
        target = production + (":resolve" if manifest["task_id"] == "S03" else ":run")
        conditions["correct-tests"] &= target in correct.get("executed", [])
        if manifest["task_id"] == "S04":
            try:
                tree = ast.parse(added)
                conditions["correct-tests"] &= not any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "sleep"
                    for node in ast.walk(tree)
                )
            except SyntaxError:
                conditions["correct-tests"] = False
        mutant_results = {
            key: run_tests({**files, **mutant}, manifest["image"], local=local)
            for key, mutant in manifest["mutants"].items()
        }
        conditions["all-mutants"] = all(
            valid_tests(result)
            and result["failures"] > 0
            and target in result.get("executed", [])
            and any(test.startswith("test_added.") for test in result.get("failure_ids", []))
            for result in mutant_results.values()
        )
        details = {"correct": correct, "mutants": mutant_results}
    elif manifest["checks"]:
        files = {**initial, **changes, "tests/test_contract.py": manifest["checks"]}
        result = run_tests(files, manifest["image"], local=local)
        conditions["behavior"] = valid_tests(result) and result["successful"]
        details["behavior"] = result
    require(set(conditions) == set(manifest["conditions"]), "验收项与合同不一致")
    return {"conditions": conditions, "details": details}


def submission(manifest: dict[str, Any], patch: Path) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="specialty-submission-") as directory:
        root = Path(directory)
        require(initialize(root, manifest["initial_files"]) == manifest["base_commit"], "base 不符")
        if patch.stat().st_size:
            subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "apply", "--", str(patch.resolve())],
                cwd=root,
                check=True,
                capture_output=True,
            )
        _, names = extract_patch(root, manifest["base_commit"])
        # 删除或符号链接也是范围/行为失败，不追随链接读取外部文件。
        return {
            name: (root / name).read_text()
            if (root / name).is_file() and not (root / name).is_symlink()
            else ""
            for name in names
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("patch", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    for name, digest in manifest["implementation"].items():
        require(
            hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() == digest,
            "判题实现已改变，必须重新入库",
        )
    worker = json.loads((args.output / "worker/worker-result.json").read_text())
    final = args.output / "worker" / worker["final"]["path"]
    print(json.dumps(grade(manifest, submission(manifest, args.patch), final.read_text())))
