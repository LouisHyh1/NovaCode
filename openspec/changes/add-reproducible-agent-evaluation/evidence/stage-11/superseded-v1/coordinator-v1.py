"""顺序执行已冻结原计划；不修改产品运行时、任务或参数。"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import importlib
import json
import os
import shutil
import sys
import tarfile
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novacode.config import load
from novacode.evaluation.assets import public_requests
from novacode.evaluation.contracts import EvaluationTask, require
from novacode.evaluation.frozen import digest, verify_manifest
from novacode.evaluation.ledger import read_records, rebuild
from novacode.evaluation.runner import run_task

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "evaluation/frozen/campaign/manifest.json"
ANCHOR = "67a35add8b42f90b2d8dc0fb842444a5b410f576f17a7e75889e2ebb70d1b32c"


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def snapshot(output: Path) -> dict[str, Any]:
    """只读从原始账本重建；零调用与缺失账本不能冒充完整成本。"""
    result = json.loads((output / "result.json").read_text())
    outer = rebuild(output / "orchestration.jsonl")
    ledger = output / "worker/ledger.jsonl"
    if not ledger.exists():
        return {"result": result, "outer": outer, "metrics": None, "cost_complete": False}
    records, truncated = read_records(ledger)
    metrics = rebuild(ledger)
    starts = [r["data"] for r in records if r["kind"] == "request_start"]
    ends = [r["data"] for r in records if r["kind"] == "request_end"]
    states = [r["data"] for r in records if r["kind"] == "initial_state"]
    require(len(states) == 1, "缺少唯一实际策略")
    prep = [r["data"] for r in records if r["kind"] == "context_preparation"]
    compactions = [r["data"] for r in records if r["kind"] == "compaction"]
    initial = states[0]
    config = result["config_id"]
    require(initial["version"] == "0.1.35", "实际运行时版本变化")
    require(initial["context_compression"] == (config != "no-compression"), "实际压缩策略错配")
    require(initial["progressive_tool_schema"] == (config != "eager-schema"), "曝光策略错配")
    require(initial["memory"] == "empty" and not initial["auxiliary_agents"], "隔离策略错配")
    require(not truncated, "账本中断；须保留并单独审计")
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    return {
        "result": result,
        "outer": outer,
        "metrics": metrics,
        "initial_state": initial,
        "request_parameters": [s["parameters"] for s in starts],
        "response_models": sorted({e.get("response", {}).get("model", "unknown") for e in ends}),
        "cost_complete": len(ends) == len(starts) and metrics["measured_tokens"] is not None,
        "raw_token_lower_bounds": {
            f: sum(e["usage"]["raw"].get(f) or 0 for e in ends) for f in fields
        },
        "raw_field_unknown_requests": {
            f: sum(e["usage"]["raw"].get(f) is None for e in ends) for f in fields
        },
        "offloads": sum(p["offloaded"] for p in prep),
        "summary_requests": sum(s["role"] == "summary" for s in starts),
        "summaries_accepted": sum(p["accepted"] for p in prep),
        "compactions": compactions,
        "schema_sets": [s["visible_tools"] for s in starts],
        "budget_stops": [r["data"] for r in records if r["kind"] == "budget_stop"],
        "errors": [r for r in records if r["kind"] in ("worker_error", "agent_error")],
    }


def archive_run(output: Path, destination: Path) -> None:
    """保留原账本和全部运行状态；业务工作树用固定镜像与完整补丁重建。"""
    require(not destination.exists(), "不能覆盖原始归档")
    workspace = output / "workspace"
    save(
        output / "reconstruction.json",
        {
            "initial": "frozen task image and base_commit",
            "changes": "submission.diff includes committed and untracked changes",
            "runtime_state": "workspace/.novacode retained in archive",
            "excluded": "unchanged initial source, .git objects, target dependencies and caches",
        },
    )
    paths = sorted(
        p
        for p in output.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and (workspace not in p.parents or workspace / ".novacode" in p.parents)
    )
    inventory = {p.relative_to(output).as_posix(): digest(p) for p in paths}
    with tarfile.open(destination, "x:gz") as archive:
        for path in paths:
            archive.add(path, arcname=path.relative_to(output), recursive=False)
    import hashlib

    with tarfile.open(destination) as archive:
        for name, sha in inventory.items():
            stream = archive.extractfile(name)
            require(stream is not None, "归档缺少产物")
            assert stream is not None
            require(hashlib.sha256(stream.read()).hexdigest() == sha, "归档产物变化")
    save(
        output / "archive.json",
        {"path": str(destination), "sha256": digest(destination), "files": inventory},
    )
    if workspace.exists():
        require(workspace.resolve().parent == output.resolve(), "清理路径越界")
        shutil.rmtree(workspace)


async def run_one(root: Path, data: dict[str, Any], row: dict[str, Any], tui: bool) -> Path:
    policy = data["policy"]
    assets = REPO / data["asset_roots"]["live" if row["task_id"].startswith("FL") else "specialty"]
    task = EvaluationTask.from_json((assets / "contracts" / (row["task_id"] + ".json")).read_text())
    cfg = next(
        p
        for p in load(str(REPO / ".novacode/config.yaml")).providers
        if p.model == policy["requested_model"] and p.protocol == policy["protocol"]
    )
    require(cfg.base_url == policy["endpoint"] and bool(cfg.api_key), "模型端点或认证缺失")
    cfg = replace(
        cfg,
        max_retries=0,
        timeout=policy["timeout_seconds"],
        thinking=True,
        context_window=policy["context_window"],
        max_output_tokens=policy["max_output_tokens"],
    )
    payload = {
        **row,
        "provider": asdict(cfg),
        "frozen_manifest": str(MANIFEST),
        "freeze_sha256": ANCHOR,
        "total_limits": data["budgets"]["total"],
        "runtime": {"sha256": data["external_files"][data["runtime_path"]]},
        "allowed_tools": data["tools"][task.task_id]["allowed"],
        **{k: policy[k] for k in ("instructions", "permissions", "usage_rule")},
    }
    output = root / "runs" / str(row["run_id"])
    # 前置失败尚未开始，另建目录留存原失败；run_id和冻结计划身份不变。
    attempt = 0
    while output.exists():
        previous = output / "result.json"
        require(previous.exists(), "中断目录须先审计，不能重启已开始条目")
        old = json.loads(previous.read_text())
        if old["termination"] is not None:
            require(old["state"] == "RECORDED", "已开始记录不完整")
            return output
        attempt += 1
        output = root / "runs" / (row["run_id"] + f".preflight-{attempt}")
    future = asyncio.create_task(
        run_task(
            task,
            public_root=assets / "public",
            acceptance_root=assets / "external",
            runtime_archive=Path(data["runtime_path"]),
            payload=payload,
            output=output,
            tui=tui,
        )
    )
    if tui:
        sys.path.insert(0, str(REPO / "evaluation/examples"))
        await importlib.import_module("stage4_live").tmux_run(
            output, future, public_requests(task, assets / "public")[0]
        )
    result = await future
    save(
        output / "execution-identity.json",
        {
            "freeze_sha256": ANCHOR,
            "sequence": row["sequence"],
            "coordinator_sha256": digest(Path(__file__)),
            "utc": datetime.now(UTC).isoformat(),
            "frozen_version": data["version"],
            "tui": tui,
        },
    )
    sample = snapshot(output)
    save(output / "sample.json", sample)
    print(
        json.dumps(
            {
                "sequence": row["sequence"],
                "task": task.task_id,
                "config": row["config_id"],
                "termination": result.termination,
                "acceptance": result.acceptance,
                "strict_success": result.strict_success,
                "metrics": sample["metrics"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    require(result.termination is not None, f"环境前置阻塞: {result.block_reason}")
    archive_run(output, root / "archives" / (output.name + ".tar.gz"))
    return output


async def execute(root: Path, limit: int, tui: bool) -> None:
    require(limit > 0 and (not tui or limit <= 3), "批次范围无效")
    root.mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)
    (root / "archives").mkdir(exist_ok=True)
    with (root / "scheduler.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data = verify_manifest(MANIFEST, ANCHOR)
        receipt = root / "identity.json"
        identity = {
            "manifest_sha256": ANCHOR,
            "coordinator_sha256": digest(Path(__file__)),
            "campaign_id": data["campaign_id"],
        }
        if receipt.exists():
            require(json.loads(receipt.read_text()) == identity, "调度身份变化")
        else:
            save(receipt, identity)
        if limit > 3:
            require((root / "first-block-reviewed.json").exists(), "首配对块尚未复核")
        count = 0
        for row in data["plan"]:
            candidates = sorted((root / "runs").glob(row["run_id"] + "*"))
            recorded = [
                p
                for p in candidates
                if (p / "result.json").exists()
                and json.loads((p / "result.json").read_text())["termination"] is not None
            ]
            require(len(recorded) <= 1, "原计划出现多个已开始结果")
            if recorded:
                require((recorded[0] / "archive.json").exists(), "已开始结果须完成归档审计")
                continue
            require(shutil.disk_usage(root).free >= 6 * 1024**3, "不足6GiB，暂停新运行")
            await run_one(root, data, row, tui)
            count += 1
            if count == limit:
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--tui", action="store_true")
    args = parser.parse_args()
    asyncio.run(execute(args.root.resolve(), args.limit, args.tui))
