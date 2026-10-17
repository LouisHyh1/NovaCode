"""有类型的归档、共享初始对象及真实恢复核验。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from novacode.evaluation.artifacts import business_state, restore_workspace
from novacode.evaluation.contracts import require
from novacode.evaluation.frozen import digest


def save(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def pack(paths: list[Path], root: Path, archive: Path) -> None:
    with archive.open("xb") as stream:
        with tarfile.open(fileobj=stream, mode="w:gz") as raw:
            for path in paths:
                raw.add(path, arcname=path.relative_to(root), recursive=False)
        stream.flush()
        os.fsync(stream.fileno())


def unpack(archive: Path, destination: Path) -> None:
    with tarfile.open(archive) as raw:
        names = raw.getnames()
        require(len(names) == len(set(names)), "归档成员重复")
        require(
            all(not Path(n).is_absolute() and ".." not in Path(n).parts for n in names),
            "归档路径越界",
        )
        require(all(m.isfile() or m.isdir() for m in raw), "归档含不允许的链接或特殊成员")
        raw.extractall(destination, filter="data")


def restore_run(archive: Path, receipt: dict[str, Any], destination: Path) -> None:
    require(digest(archive) == receipt["sha256"], "原始归档指纹变化")
    destination.mkdir(parents=True, exist_ok=False)
    unpack(archive, destination)
    actual = {
        p.relative_to(destination).as_posix(): digest(p)
        for p in destination.rglob("*")
        if p.is_file()
    }
    require(actual == receipt["files"], "归档成员或内容指纹变化")
    reference = json.loads((destination / "baseline-reference.json").read_text())
    baseline_archive = Path(reference["path"])
    require(digest(baseline_archive) == reference["sha256"], "可信初始对象归档变化")
    baseline = destination / "restored-baseline"
    baseline.mkdir()
    unpack(baseline_archive, baseline)
    require(digest(baseline / "manifest.json") == reference["manifest_sha256"], "初始基线身份变化")
    workspace = destination / "restored-workspace"
    restore_workspace(baseline, (destination / "submission.diff").read_text(), workspace)
    require(
        business_state(workspace) == json.loads((destination / "business-state.json").read_text()),
        "恢复业务内容/类型/模式不一致",
    )


def archive_run(output: Path, destination: Path) -> None:
    """任何失败保留完整现场；只有真实重建一致才释放可再生副本。"""
    require(not destination.exists(), "不能覆盖原始归档")
    workspace, baseline = output / "workspace", output / "baseline"
    require(workspace.is_dir(), "缺少业务现场，禁止声称完整归档")
    require((output / "submission.diff").is_file(), "补丁缺失，保留完整工作区与容器")
    require((baseline / "manifest.json").is_file(), "缺少可信类型化基线，保留现场")
    state = business_state(workspace)
    save(output / "business-state.json", state)
    baseline_sha = digest(baseline / "manifest.json")
    storage = destination.parent.parent / "baselines"
    storage.mkdir(parents=True, exist_ok=True)
    baseline_archive = storage / (baseline_sha + ".tar.gz")
    if not baseline_archive.exists():
        pack(sorted(p for p in baseline.rglob("*") if p.is_file()), baseline, baseline_archive)
    save(
        output / "baseline-reference.json",
        {
            "schema_version": 2,
            "path": str(baseline_archive.resolve()),
            "sha256": digest(baseline_archive),
            "manifest_sha256": baseline_sha,
            "base_commit": json.loads((baseline / "manifest.json").read_text())["base_commit"],
        },
    )
    paths = sorted(
        p
        for p in output.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and baseline not in p.parents
        and (workspace not in p.parents or workspace / ".novacode" in p.parents)
    )
    inventory = {p.relative_to(output).as_posix(): digest(p) for p in paths}
    pack(paths, output, destination)
    receipt = {
        "schema_version": 2,
        "path": str(destination.resolve()),
        "sha256": digest(destination),
        "files": inventory,
        "baseline_manifest_sha256": baseline_sha,
        "reconstruction_verified": False,
    }
    with tempfile.TemporaryDirectory(prefix="novacode-restore-check-") as folder:
        restore_run(destination, receipt, Path(folder) / "restored")
    receipt["reconstruction_verified"] = True
    save(output / "archive.json", receipt)
    # 新失败现场的容器在通过归档后才释放；历史已有归档不调用此清理路径。
    retained = output / "retained-container.json"
    if retained.exists():
        data = json.loads(retained.read_text())
        require(
            data["stopped"] and data["container"].startswith("novacode-eval-"),
            "不允许释放非所属停止容器",
        )
        running = subprocess.check_output(
            ["docker", "inspect", "--format", "{{.State.Running}}", data["container"]]
        ).strip()
        require(running == b"false", "容器仍在运行")
        subprocess.run(["docker", "rm", data["container"]], check=True, stdout=subprocess.DEVNULL)
    require(
        workspace.resolve().parent == output.resolve()
        and baseline.resolve().parent == output.resolve(),
        "清理路径越界",
    )
    shutil.rmtree(workspace)
    shutil.rmtree(baseline)
