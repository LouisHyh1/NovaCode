"""生成公开包与外部验收包；不运行模型，不覆盖已存在的资产。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from definitions import cases

from novacode.evaluation.contracts import Asset, EvaluationTask, json_text

BASE_ENV = {
    "GIT_AUTHOR_NAME": "Specialty Builder",
    "GIT_AUTHOR_EMAIL": "builder@example.invalid",
    "GIT_COMMITTER_NAME": "Specialty Builder",
    "GIT_COMMITTER_EMAIL": "builder@example.invalid",
    "GIT_AUTHOR_DATE": "2026-10-10T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-10-10T00:00:00Z",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


def write_files(root: Path, files: dict[str, str]) -> None:
    from novacode.evaluation.contracts import relative_path, require

    for name, content in files.items():
        relative_path(name)
        path = root / name
        require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve()), "文件越界")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def initialize(root: Path, files: dict[str, str]) -> str:
    write_files(root, files)
    env = {**os.environ, **BASE_ENV}
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "--", "."],
        ["-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "fixed initial fixture"],
    ):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, env=env).decode().strip()


def asset(root: Path, name: str, data: Any) -> Asset:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return Asset(name, hashlib.sha256(path.read_bytes()).hexdigest())


def build(output: Path, image: str) -> tuple[EvaluationTask, ...]:
    output.mkdir(parents=True, exist_ok=False)
    public, hidden = output / "public", output / "external"
    (output / "contracts").mkdir()
    tasks = []
    for case in cases():
        with tempfile.TemporaryDirectory(prefix="specialty-base-") as name:
            base = initialize(Path(name), case.files)
        initial = asset(
            public,
            f"{case.task_id}/initial.json",
            {
                "schema_version": 1,
                "base_commit": base,
                "files": case.files,
            },
        )
        requests = asset(
            public,
            f"{case.task_id}/requests.json",
            {
                "schema_version": 1,
                "requests": case.requests,
            },
        )
        environment = asset(
            public,
            f"{case.task_id}/environment.json",
            {
                "schema_version": 1,
                "image": image,
                "resources": {"cpus": 1, "memory_bytes": 536870912, "pids": 64},
                "target_python": "python",
                "test_command": "python -m unittest discover -s tests -v",
                "tool_records": case.records,
                "task_id": case.task_id,
                "fault_rule": {
                    "S05": "first-valid-grep-v1",
                    "S06": "first-standard-test-timeout-v1",
                }.get(case.task_id),
                "pressure_dimensions": {
                    "script_requests": len(case.requests),
                    "public_bytes": sum(len(v.encode()) for v in case.files.values()),
                },
            },
        )
        conditions = ["facts", "scope"] if case.answers else ["behavior", "scope"]
        if case.category == "test-addition":
            conditions = ["correct-tests", "all-mutants", "scope"]
        acceptance = asset(
            hidden,
            f"{case.task_id}/acceptance.json",
            {
                "schema_version": 1,
                "task_id": case.task_id,
                "conditions": conditions,
                "initial_files": case.files,
                "reference": case.reference,
                "checks": case.checks,
                "answers": case.answers,
                "mutants": case.mutants,
                "allowed_paths": case.allowed,
                "base_commit": base,
                "image": image,
                "command": [
                    sys.executable,
                    str(Path(__file__).with_name("judge.py").resolve()),
                    "--manifest",
                    str((hidden / f"{case.task_id}/acceptance.json").resolve()),
                ],
                "timeout_seconds": 180,
                "implementation": {
                    name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in ("judge.py", "test_driver.py", "build.py", "definitions.py")
                },
            },
        )
        if case.task_id == "S12":
            conditions.insert(1, "behavior")
            manifest_path = hidden / acceptance.path
            manifest = json.loads(manifest_path.read_text())
            manifest["conditions"] = conditions
            acceptance = asset(hidden, acceptance.path, manifest)
        task = EvaluationTask(
            task_id=case.task_id,
            candidate_id=case.task_id + "-v1",
            source="specialty",
            suite="development",
            family_id="specialty-v1/" + case.task_id,
            problem_keys=("defect:specialty-v1/" + case.task_id,),
            repository="specialty/" + case.task_id,
            source_revision="specialty-v1",
            base_commit=base,
            difficulty=case.difficulty,
            category=case.category,
            pressure_tags=tuple(case.pressure),
            budget_category="specialty-" + case.category,
            initial_state=initial,
            public_input=requests,
            environment=environment,
            acceptance_assets=(acceptance,),
            acceptance_conditions=tuple(conditions),
            allowed_paths=tuple(case.allowed),
            builder_id="specialty-builder-v1",
        )
        (output / "contracts" / (case.task_id + ".json")).write_text(json_text(task) + "\n")
        tasks.append(task)
    return tuple(tasks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    build(args.output, args.image)
