"""逐镜像安装最终运行时并检查目标环境不变；不创建模型Provider。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from novacode.evaluation.frozen import digest, load_tasks  # noqa: E402
from novacode.evaluation.runner import command  # noqa: E402


async def deploy(runtime: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    tasks = tuple(
        t
        for source in ("live", "specialty")
        for t in load_tasks(REPO / "evaluation/frozen" / (source + "-assets"))
    )
    images = {}
    for task in tasks:
        root = REPO / "evaluation/frozen" / (task.source + "-assets/public")
        env = json.loads((root / task.environment.path).read_text())
        images[env["image"]] = env["resources"]
    require_complete = len(tasks) == 48
    if not require_complete:
        raise ValueError("必须先完成48道冻结合同")
    proofs = {}
    for index, (image, resources) in enumerate(sorted(images.items())):
        name = "nova-freeze-deploy-" + uuid4().hex
        folder = output / str(index)
        folder.mkdir()
        created = False
        try:
            await command(
                "docker",
                "create",
                "--name",
                name,
                "--network",
                "none",
                "--cpus",
                str(resources["cpus"]),
                "--memory",
                str(resources["memory_bytes"]),
                "--memory-swap",
                str(resources["memory_bytes"]),
                "--pids-limit",
                str(resources["pids"]),
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                image,
                "tail",
                "-f",
                "/dev/null",
            )
            created = True
            await command("docker", "start", name)
            inspect = await command("docker", "inspect", name)
            (folder / "container.json").write_bytes(inspect)
            target = (
                "command -v python; python --version"
                "; python -m pip freeze; git rev-par"
                "se HEAD; git status --porcelain"
            )
            before = await command("docker", "exec", "-w", "/testbed", name, "bash", "-lc", target)
            (folder / "target-before.txt").write_bytes(before)
            await command("docker", "cp", str(runtime), name + ":/runtime.tar")
            await command(
                "docker", "exec", name, "tar", "--no-same-owner", "-xf", "/runtime.tar", "-C", "/"
            )
            installed = await command(
                "docker",
                "exec",
                name,
                "sh",
                "-c",
                (
                    "/opt/novacode/uv venv /opt/novacode"
                    "/venv --python /opt/novacode/python"
                    "/bin/python3.12 && /opt/novacode/uv"
                    " pip install --offline --no-index -"
                    "-find-links /opt/novacode/wheels --"
                    "python /opt/novacode/venv/bin/pytho"
                    "n novacode"
                ),
            )
            (folder / "installed.txt").write_bytes(installed)
            smoke = await command(
                "docker",
                "exec",
                name,
                "/opt/novacode/venv/bin/python",
                "/opt/novacode/tool-smoke.py",
            )
            (folder / "tool-smoke.txt").write_bytes(smoke)
            after = await command("docker", "exec", "-w", "/testbed", name, "bash", "-lc", target)
            (folder / "target-after.txt").write_bytes(after)
            if before != after:
                raise ValueError("最终安装改变目标环境")
            proofs[image] = {
                "passed": True,
                "evidence": str(folder),
                "evidence_files": {p.name: digest(p) for p in sorted(folder.iterdir())},
            }
            print("DEPLOYED", image, flush=True)
        finally:
            if created:
                await command("docker", "rm", "-f", name)
    result = {
        "schema_version": 1,
        "runtime_sha256": digest(runtime),
        "model_calls": 0,
        "images": proofs,
        "tasks": {
            task.task_id: {
                "passed": True,
                "image": json.loads(
                    (
                        REPO
                        / "evaluation/frozen"
                        / (task.source + "-assets/public")
                        / task.environment.path
                    ).read_text()
                )["image"],
            }
            for task in tasks
        },
        "coverage": (
            "最终wheel逐镜像独立安装/目标依赖和源码不变/六个正式工具；逐题b"
            "ase与正确负例另见入库证据"
        ),
    }
    (output / "deployment.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(deploy(args.runtime.resolve(), args.output.resolve()))
