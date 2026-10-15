"""完成48题身份审计并生成432行原计划；没有模型执行命令。"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import zipfile
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from novacode.evaluation.contracts import Asset, fingerprint, require  # noqa: E402
from novacode.evaluation.corpus import SelectionLedger  # noqa: E402
from novacode.evaluation.frozen import (  # noqa: E402
    campaign_contract,
    corpus_identity,
    digest,
    input_guard,
    load_tasks,
    plan_runs,
    source_files,
    tool_identity,
    validate_policy,
    verify_manifest,
)

CHANGE = "openspec/changes/add-reproducible-agent-evaluation"
ROOTS = {"live": "evaluation/frozen/live-assets", "specialty": "evaluation/frozen/specialty-assets"}
LEDGERS = {
    "live": "evaluation/frozen/live-assets/selection.jsonl",
    "specialty": CHANGE + "/evidence/stage-10/specialty-admission/selection.jsonl",
}


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def policy() -> dict[str, Any]:
    pilot = json.loads((REPO / "evaluation/pilot/policy.json").read_text())
    data = {
        key: pilot[key]
        for key in (
            "schema_version",
            "seed",
            "concurrency",
            "requested_model",
            "served_model",
            "endpoint",
            "thinking",
            "sdk_max_retries",
            "context_window",
            "max_output_tokens",
            "timeout_seconds",
            "instructions",
            "model_identity_source",
        )
    }
    data.update(
        protocol="anthropic",
        thinking_budget_tokens=2048,
        thinking_rule="only-without-tool-history; endpoint-budget-ignored",
        input_admission_rule="utf8-bytes-conservative",
        unsupported_seed=True,
        sampling="not-sent-endpoint-default",
        remote_cache="not-resettable",
        remote_version="mutable-alias-no-immutable-weight-pin",
        permissions={"allow": ["Write", "Edit", "Bash"], "deny": []},
        ask="deterministic-deny",
        hooks=[],
        memory="empty-isolated",
        session="fresh-per-run",
        roles=["main"],
        tool_parallelism="read-only-parallel",
        usage_rule={
            "rule_id": "deepseek-anthropic-cache-separate-stage9-v1",
            "protocol": "anthropic",
            "cache_mode": "separate",
            "evidence_sha256": digest(REPO / CHANGE / "evidence/stage-9/usage-probe.json"),
        },
        analysis={
            "method": "task-paired-percentile-bootstrap-v1",
            "resamples": 10000,
            "confidence": 0.95,
            "seed": 20261012,
            "primary_comparisons": [["full", "no-compression"], ["full", "eager-schema"]],
            "unit": "task-three-repeat-means",
            "degenerate": "mark-not-certainty",
            "p90": "nearest-rank",
            "sources": "separate",
        },
    )
    return data


def build(output: Path, runtime: Path, wheel: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    tasks, _ = corpus_identity(REPO, ROOTS, LEDGERS)
    p = policy()
    budget_path = CHANGE + "/evidence/stage-9/calibrated-budgets.json"
    budgets = json.loads((REPO / budget_path).read_text())
    validate_policy(p, budgets, tasks)
    campaign_id = "novacode-frozen-20261012-v1"
    tasks, corpus = corpus_identity(REPO, ROOTS, LEDGERS)
    code = source_files(REPO)
    with zipfile.ZipFile(wheel) as package:
        require(
            not any("evaluation/frozen/" in n or "acceptance" in n for n in package.namelist()),
            "wheel含隐藏资产",
        )
        for name, sha in code.items():
            if name.startswith("src/"):
                import hashlib

                require(
                    hashlib.sha256(package.read(name.removeprefix("src/"))).hexdigest() == sha,
                    "wheel与冻结源码不一致",
                )
    tools = {t.task_id: tool_identity(t, REPO / ROOTS[t.source]) for t in tasks}
    cache = Path.home() / ".cache/novacode-evaluation/stage1"
    grader = cache / "SWE-bench-Live"
    require(
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=grader, text=True).strip()
        == "ad79b850f15e33992e96f03f6e97f05ddf9aa0be",
        "官方grader commit变化",
    )
    require(
        not subprocess.check_output(["git", "status", "--porcelain"], cwd=grader).strip(),
        "官方grader有修改",
    )
    external = {
        str(path.resolve()): digest(path)
        for path in [
            runtime,
            wheel,
            cache / "verified.parquet",
            cache / "grader-requirements.lock",
            REPO / CHANGE / "evidence/stage-9/usage-probe.json",
            *sorted((grader / "swebench").rglob("*.py")),
        ]
    }
    deployed = json.loads((REPO / CHANGE / "evidence/stage-10/runtime-deployment.json").read_text())
    require(
        deployed["runtime_sha256"] == digest(runtime)
        and set(deployed["tasks"]) == {t.task_id for t in tasks},
        "最终运行时未在全题部署核验",
    )
    require(all(r["passed"] for r in deployed["tasks"].values()), "最终运行时部署失败")
    manifest = {
        "schema_version": 1,
        "state": "FROZEN_VALID",
        "formal_model_calls": 0,
        "version": version("novacode"),
        "campaign_id": campaign_id,
        "repository_root": str(REPO),
        "source_base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "source_files": code,
        "asset_roots": ROOTS,
        "selection_ledgers": LEDGERS,
        "policy": p,
        "budgets": budgets,
        "budget_evidence_path": budget_path,
        "budget_evidence_sha256": digest(REPO / budget_path),
        "protocol_path": CHANGE + "/evaluation-protocol.md",
        "protocol_sha256": digest(REPO / CHANGE / "evaluation-protocol.md"),
        "corpus": corpus,
        "tools": tools,
        "input_guards": {
            t.task_id: input_guard(t, REPO / ROOTS[t.source], tools[t.task_id], p) for t in tasks
        },
        "plan": plan_runs(tasks, budgets, campaign_id, p["seed"]),
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
        "limits": {
            "concurrency": 1,
            "live": {"cpus": 2, "memory_bytes": 4294967296, "pids": 256},
            "specialty": {"cpus": 1, "memory_bytes": 536870912, "pids": 64},
        },
        "unknown_items": budgets["unknown_items"],
        "selection_by_agent_success": False,
    }
    for source in ("live", "specialty"):
        root = REPO / ROOTS[source]
        members = load_tasks(root)
        ledger = SelectionLedger(
            REPO / LEDGERS[source],
            members,
            root / "public",
            root / "external",
            frozenset(t.budget_category for t in members),
        )
        states, _ = ledger.replay()
        require(set(states.values()) == {"ADMITTED"}, "禁止覆盖已有冻结")
        for task in members:
            marker = root / "external" / task.task_id / "freeze.json"
            save(
                marker,
                {
                    "schema_version": 1,
                    "task_sha256": fingerprint(task),
                    "suite": "frozen",
                    "campaign_id": campaign_id,
                },
            )
            ledger.append(
                task.task_id,
                "FREEZE",
                "campaign-frozen",
                "完整身份、预算与原计划固定",
                evidence=Asset(marker.relative_to(root / "external").as_posix(), digest(marker)),
            )
    tasks, corpus = corpus_identity(REPO, ROOTS, LEDGERS)
    manifest["corpus"] = corpus
    path = output / "manifest.json"
    manifest["campaign"] = asdict(campaign_contract(manifest, tasks))
    save(path, manifest)
    save(output / "campaign.json", manifest["campaign"])
    (output / "manifest.sha256").write_text(digest(path) + "\n")
    verify_manifest(path, digest(path))
    save(
        output / "audit.json",
        {
            "schema_version": 1,
            "state": "FROZEN_VALID",
            "manifest_sha256": digest(path),
            "tasks": len(tasks),
            "planned": len(manifest["plan"]),
            "formal_model_calls": 0,
            "all_states": "PLANNED",
            "source_equals_wheel": True,
        },
    )
    print("FROZEN_VALID: 48 tasks, 432 PLANNED, 0 model calls")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "verify"))
    parser.add_argument("--output", type=Path, default=REPO / "evaluation/frozen/campaign")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    if args.mode == "build":
        build(args.output.resolve(), args.runtime.resolve(), args.wheel.resolve())
    else:
        verify_manifest(
            args.output / "manifest.json", (args.output / "manifest.sha256").read_text().strip()
        )
        print("FROZEN_VALID verified; model calls = 0")
