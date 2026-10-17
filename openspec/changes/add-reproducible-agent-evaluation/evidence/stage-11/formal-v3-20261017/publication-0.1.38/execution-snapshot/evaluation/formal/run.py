"""顺序执行已冻结原计划；不修改产品运行时、任务或参数。"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.formal.preservation import archive_run
from novacode.config import load
from novacode.evaluation.compact_manifest import check_disk, validate_authorization
from novacode.evaluation.contracts import EvaluationTask, require
from novacode.evaluation.frozen import digest, verify_manifest
from novacode.evaluation.ledger import read_records, rebuild
from novacode.evaluation.runner import run_task

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "evaluation/frozen/campaign-v2/manifest.json"


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
    execution_identity = json.loads((output / "execution-identity.json").read_text())
    expected_version = execution_identity["frozen_version"]
    require(initial["version"] == expected_version, "实际运行时版本变化")
    if "max_iterations" in execution_identity:
        require(
            initial.get("max_iterations") == execution_identity["max_iterations"] == 25
            and initial.get("iteration_scope") == "per-agent-run",
            "实际迭代装配变化",
        )
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


async def run_one(
    root: Path,
    data: dict[str, Any],
    row: dict[str, Any],
    *,
    manifest_path: Path = MANIFEST,
    authorization: dict[str, Any] | None = None,
) -> Path:
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
        "frozen_manifest": str(manifest_path),
        "freeze_sha256": digest(manifest_path),
        "authorization": authorization,
        "max_iterations": policy["max_iterations"],
        "initial_baseline_sha256": data["baselines"][task.task_id]["sha256"],
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
            tui=False,
        )
    )
    result = await future
    save(
        output / "execution-identity.json",
        {
            "freeze_sha256": digest(manifest_path),
            "sequence": row["sequence"],
            "coordinator_sha256": digest(Path(__file__)),
            "utc": datetime.now(UTC).isoformat(),
            "frozen_version": data["version"],
            "tui": False,
            "max_iterations": 25,
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


async def execute(
    root: Path, limit: int, *, manifest_path: Path = MANIFEST, authorization: Path | None = None
) -> None:
    require(limit > 0, "批次范围无效")
    anchor = manifest_path.with_suffix(".sha256").read_text().strip()
    data = verify_manifest(manifest_path, anchor)
    require(data["schema_version"] == 2, "历史队列已停止，禁止恢复旧模型计划")
    approval = json.loads(authorization.read_text()) if authorization else None
    validate_authorization(data, approval, anchor)
    root.mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)
    (root / "archives").mkdir(exist_ok=True)
    with (root / "scheduler.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = root / "identity.json"
        identity = {
            "manifest_sha256": anchor,
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
            check_disk(root, data)
            await run_one(root, data, row, manifest_path=manifest_path, authorization=approval)
            count += 1
            if count == limit:
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(
        execute(
            args.root.resolve(),
            args.limit,
            manifest_path=args.manifest.resolve(),
            authorization=args.authorization.resolve(),
        )
    )
