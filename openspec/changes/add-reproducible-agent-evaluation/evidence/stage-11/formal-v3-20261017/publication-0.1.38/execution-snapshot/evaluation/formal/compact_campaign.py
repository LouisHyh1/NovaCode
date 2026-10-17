"""只准备精简 v3：元数据抽样、离线部署及零模型调用冻结。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
import zipfile
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from evaluation.formal.preservation import pack  # noqa: E402
from evaluation.formal.run import save  # noqa: E402
from evaluation.frozen.freeze import policy  # noqa: E402
from novacode.evaluation.compact import PARENT_SHA, selection  # noqa: E402
from novacode.evaluation.compact_manifest import (  # noqa: E402
    compact_budgets,
    coordinator_files,
    subset,
)
from novacode.evaluation.frozen import (  # noqa: E402
    campaign_contract,
    corpus_identity,
    digest,
    input_guard,
    plan_runs,
    source_files,
    tool_identity,
    validate_policy,
    verify_manifest,
)
from novacode.evaluation.runner import command, prepare_specialty  # noqa: E402

CHANGE = "openspec/changes/add-reproducible-agent-evaluation"
PARENT = REPO / "evaluation/frozen/campaign-v2/manifest.json"
HISTORY_GATE = CHANGE + "/evidence/stage-11/history-audit-20261016/history-audited.json"
CHAIN_GATE = CHANGE + "/evidence/stage-11/evidence-chain-20261016/validation.json"


def pool() -> tuple[dict[str, Any], tuple[Any, ...], dict[str, Any]]:
    assert digest(PARENT) == PARENT_SHA
    parent = json.loads(PARENT.read_text())
    tasks, corpus = corpus_identity(REPO, parent["asset_roots"], parent["selection_ledgers"])
    assert corpus == parent["corpus"], "父任务资产或资格证据变化"
    return parent, tasks, corpus


def select(output: Path) -> None:
    assert json.loads((REPO / HISTORY_GATE).read_text())["state"] == "HISTORY_AUDITED"
    assert json.loads((REPO / CHAIN_GATE).read_text())["state"] == "EVIDENCE_CHAIN_VALID"
    _, tasks, _ = pool()
    chosen = selection([asdict(t) for t in tasks], PARENT_SHA)
    output.mkdir(parents=True, exist_ok=False)
    save(output / "selection.json", chosen)
    (output / "selection.sha256").write_text(digest(output / "selection.json") + "\n")
    print(json.dumps(chosen["selected"], ensure_ascii=False))


async def deploy(
    selection_path: Path, runtime: Path, output: Path, baseline_cache: Path | None = None
) -> None:
    chosen = json.loads(selection_path.read_text())
    parent, tasks, _ = pool()
    assert chosen == selection([asdict(t) for t in tasks], PARENT_SHA)
    selected = {name for names in chosen["selected"].values() for name in names}
    output.mkdir(parents=True, exist_ok=False)
    baselines = {}
    proofs = {}
    cached = json.loads(baseline_cache.read_text())["baselines"] if baseline_cache else {}
    for task in tasks:
        if task.task_id not in selected:
            continue
        folder = output / task.task_id
        folder.mkdir()
        root = REPO / parent["asset_roots"][task.source]
        environment = json.loads((root / "public" / task.environment.path).read_text())
        resources = environment["resources"]
        name = "novacode-v3-deploy-" + uuid4().hex
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
                "--pids-limit",
                str(resources["pids"]),
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                environment["image"],
                "tail",
                "-f",
                "/dev/null",
            )
            created = True
            await command("docker", "start", name)
            target = (
                "import json,subprocess,sys; print(json.dumps({'python':sys.executable,"
                "'version':sys.version,'dependencies':subprocess.check_output("
                "[sys.executable,'-m','pip','freeze']).decode(),"
                "'base':subprocess.check_output(['git','rev-parse','HEAD']).decode(),"
                "'status':subprocess.check_output(['git','status','--porcelain']).decode()},"
                "sort_keys=True))"
            )
            before = await command("docker", "exec", "-w", "/testbed", name, "python", "-c", target)
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
                "/opt/novacode/uv venv /opt/novacode/venv "
                "--python /opt/novacode/python/bin/python3.12 && "
                "/opt/novacode/uv pip install --offline --no-index "
                "--find-links /opt/novacode/wheels --python /opt/novacode/venv/bin/python novacode",
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
            after = await command("docker", "exec", "-w", "/testbed", name, "python", "-c", target)
            assert before == after, "最终运行时改变目标依赖/源码"
            (folder / "target-before.json").write_bytes(before)
            (folder / "target-after.json").write_bytes(after)
            workspace = "/testbed"
            if task.source == "specialty":
                initial = json.loads((root / "public" / task.initial_state.path).read_text())
                workspace = await prepare_specialty(name, initial, task)
            code = (
                "import json,inspect; from pathlib import Path; "
                "from novacode.agent import Agent, MAX_ITERATIONS; "
                "from novacode.evaluation.artifacts import capture_baseline; "
                "assert inspect.signature(Agent).parameters['max_turns'].default==0; "
                "assert MAX_ITERATIONS==25; "
                "d=capture_baseline(Path("
                + repr(workspace)
                + "),"
                + repr(task.base_commit)
                + ",Path('/run/novacode-baseline')); "
                "print(json.dumps({'tree':d['tree'],'entries':len(d['entries'])}))"
            )
            metadata = json.loads(
                await command(
                    "docker", "exec", name, "/opt/novacode/venv/bin/python", "-c", code, timeout=600
                )
            )
            if task.task_id in cached:
                # 新运行时仍实际生成基线；仅在清单逐字相同后复用不可变对象归档。
                existing = cached[task.task_id]
                fresh = await command(
                    "docker", "exec", name, "cat", "/run/novacode-baseline/manifest.json"
                )
                assert hashlib.sha256(fresh).hexdigest() == existing["sha256"]
                assert digest(Path(existing["path"])) == existing["sha256"]
                assert digest(Path(existing["archive_path"])) == existing["archive_sha256"]
                assert all(existing[k] == v for k, v in metadata.items())
                baselines[task.task_id] = existing
            else:
                baseline = folder / "baseline"
                await command(
                    "docker", "cp", name + ":/run/novacode-baseline", str(baseline), timeout=300
                )
                archive = folder / "baseline.tar.gz"
                pack(sorted(p for p in baseline.rglob("*") if p.is_file()), baseline, archive)
                baselines[task.task_id] = {
                    "schema_version": 2,
                    "task_id": task.task_id,
                    "path": str((baseline / "manifest.json").resolve()),
                    "sha256": digest(baseline / "manifest.json"),
                    "archive_path": str(archive.resolve()),
                    "archive_sha256": digest(archive),
                    **metadata,
                }
            proofs[task.task_id] = {
                "passed": True,
                "image": environment["image"],
                "target_unchanged": True,
                "max_iterations": 25,
                "model_calls": 0,
            }
            save(
                folder / "receipt.json",
                {"task": proofs[task.task_id], "baseline": baselines[task.task_id]},
            )
            print("DEPLOYED", task.task_id, metadata["entries"], flush=True)
        finally:
            if created:
                await command("docker", "rm", "-f", name)
    save(
        output / "deployment.json",
        {
            "schema_version": 2,
            "state": "DEPLOYMENT_VALID",
            "runtime_sha256": digest(runtime),
            "baselines": baselines,
            "tasks": proofs,
            "model_calls": 0,
        },
    )


def freeze(
    output: Path,
    selection_path: Path,
    deployment_path: Path,
    runtime: Path,
    wheel: Path,
    campaign_id: str = "novacode-compact-20261016-v3",
) -> None:
    parent, members, corpus = pool()
    chosen = json.loads(selection_path.read_text())
    assert chosen == selection([asdict(t) for t in members], PARENT_SHA)
    selected = {name for names in chosen["selected"].values() for name in names}
    tasks = tuple(t for t in members if t.task_id in selected)
    deployed = json.loads(deployment_path.read_text())
    assert deployed["state"] == "DEPLOYMENT_VALID" and deployed["runtime_sha256"] == digest(runtime)
    assert set(deployed["tasks"]) == selected and all(
        row["passed"] for row in deployed["tasks"].values()
    )
    code = source_files(REPO)
    with zipfile.ZipFile(wheel) as package:
        for name, sha in code.items():
            if name.startswith("src/"):
                assert hashlib.sha256(package.read(name.removeprefix("src/"))).hexdigest() == sha
    p = {**policy(), "max_iterations": 25, "iteration_scope": "per-agent-run"}
    budgets = compact_budgets(parent["budgets"], tasks)
    validate_policy(p, budgets, tasks)
    output.mkdir(parents=True, exist_ok=False)
    save(output / "budgets.json", budgets)
    external = {
        name: sha
        for name, sha in parent["external_files"].items()
        if name
        not in (
            parent["runtime_path"],
            next(n for n in parent["external_files"] if n.endswith(".whl")),
        )
    }
    external.update({str(runtime.resolve()): digest(runtime), str(wheel.resolve()): digest(wheel)})
    for baseline in deployed["baselines"].values():
        external[baseline["archive_path"]] = baseline["archive_sha256"]
    chain = json.loads((REPO / CHAIN_GATE).read_text())
    storage_path = (REPO / CHAIN_GATE).with_name("storage-measurement.json")
    storage = json.loads(storage_path.read_text())
    assert storage["state"] == "STORAGE_MEASURED"
    measured = storage["peak_increment_bytes"]
    conservative = max(2 * measured, chain["conservative_peak_working_bytes"])
    data = {
        "schema_version": 2,
        "state": "FROZEN_VALID",
        "formal_model_calls": 0,
        "campaign_id": campaign_id,
        "version": version("novacode"),
        "repository_root": str(REPO),
        "source_base_commit": parent["source_base_commit"],
        "source_files": code,
        "coordinator_files": coordinator_files(REPO),
        "asset_roots": parent["asset_roots"],
        "selection_ledgers": parent["selection_ledgers"],
        "corpus": subset(corpus, selected),
        "parent_manifest": {"path": str(PARENT.relative_to(REPO)), "sha256": PARENT_SHA},
        "selection": chosen,
        "policy": p,
        "budgets": budgets,
        "budget_evidence_path": str((output / "budgets.json").relative_to(REPO)),
        "budget_evidence_sha256": digest(output / "budgets.json"),
        "protocol_path": CHANGE + "/evaluation-protocol.md",
        "protocol_sha256": digest(REPO / CHANGE / "evaluation-protocol.md"),
        "tools": {
            t.task_id: tool_identity(t, REPO / parent["asset_roots"][t.source]) for t in tasks
        },
        "plan": plan_runs(tasks, budgets, campaign_id, p["seed"], expected_tasks=24),
        "runtime_path": str(runtime.resolve()),
        "external_files": external,
        "sdk_versions": {name: version(name) for name in ("anthropic", "mcp", "httpx", "openai")},
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "docker": subprocess.check_output(
                ["docker", "version", "--format", "{{.Server.Version}}"], text=True
            ).strip(),
        },
        "limits": parent["limits"],
        "baseline_contract": "typed-git-initial-v2",
        "baselines": deployed["baselines"],
        "disk_admission": {
            "minimum_free_bytes": conservative + 2 * 1024**3,
            "measured_peak_bytes": measured,
            "basis": CHAIN_GATE,
            "headroom_bytes": 2 * 1024**3,
        },
        "unknown_items": parent["unknown_items"],
        "selection_by_agent_success": False,
        "input_guards": {},
        "authorization": "requires-separate-explicit-user-confirmation",
    }
    for key, path in (
        ("history_gate", REPO / HISTORY_GATE),
        ("chain_gate", REPO / CHAIN_GATE),
        ("deployment_gate", deployment_path),
        ("storage_gate", storage_path),
    ):
        data[key] = {"path": str(path.relative_to(REPO)), "sha256": digest(path)}
    data["input_guards"] = {
        t.task_id: input_guard(
            t, REPO / parent["asset_roots"][t.source], data["tools"][t.task_id], p
        )
        for t in tasks
    }
    data["campaign"] = asdict(campaign_contract(data, tasks))
    save(output / "manifest.json", data)
    anchor = digest(output / "manifest.json")
    (output / "manifest.sha256").write_text(anchor + "\n")
    verify_manifest(output / "manifest.json", anchor)
    save(
        output / "audit.json",
        {
            "state": "FROZEN_VALID",
            "tasks": 24,
            "planned": 216,
            "formal_model_calls": 0,
            "manifest_sha256": anchor,
            "authorization_present": False,
        },
    )
    print("FROZEN_VALID", anchor, "model calls = 0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("select", "deploy", "freeze", "verify"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--baseline-cache", type=Path)
    parser.add_argument("--campaign-id", default="novacode-compact-20261016-v3")
    args = parser.parse_args()
    if args.action == "select":
        select(args.output.resolve())
    elif args.action == "deploy":
        asyncio.run(
            deploy(
                args.selection.resolve(),
                args.runtime.resolve(),
                args.output.resolve(),
                args.baseline_cache,
            )
        )
    elif args.action == "freeze":
        freeze(
            args.output.resolve(),
            args.selection.resolve(),
            args.deployment.resolve(),
            args.runtime.resolve(),
            args.wheel.resolve(),
            args.campaign_id,
        )
    else:
        verify_manifest(
            args.output / "manifest.json", (args.output / "manifest.sha256").read_text().strip()
        )
        print("FROZEN_VALID verified; model calls = 0")
