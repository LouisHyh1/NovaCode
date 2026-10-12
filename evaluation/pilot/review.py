"""只读重建先导证据、按类别校准预算；不创建冻结计划或调用模型。"""

from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import tarfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from novacode.config import load
from novacode.evaluation.contracts import EvaluationTask, require
from novacode.evaluation.ledger import read_records, rebuild
from novacode.evaluation.pilot import run_metrics
from novacode.evaluation.usage import count as token_count

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evaluation.pilot.run import REPO, digest, save  # noqa: E402

FROZEN_COUNTS = {
    "live-easy": 8,
    "live-medium": 12,
    "live-hard": 4,
    "specialty-localization": 4,
    "specialty-test-addition": 4,
    "specialty-tool-failure": 4,
    "specialty-long-context": 6,
    "specialty-tool-discovery": 6,
}


def calibration(samples: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, Any] = {}
    for category, count in FROZEN_COUNTS.items():
        cohort = [s for s in samples if s["budget_category"] == category]
        require(bool(cohort), "缺少类别先导证据")
        observed = {
            "seconds": max(s["metrics"]["main_end_to_end_seconds"] for s in cohort),
            "tokens": max(s["metrics"]["known_token_lower_bound"] for s in cohort),
            "provider_calls": max(s["metrics"]["provider_calls"] for s in cohort),
            "tool_calls": max(s["metrics"]["tool_calls"] for s in cohort),
            "single_request_tokens": max(
                max(s["max_request_tokens"], s["max_request_bytes"] + 4096) for s in cohort
            ),
            "cleanup_seconds": max(s["outer_metrics"]["phase_seconds"]["cleanup"] for s in cohort),
        }
        # 保留全部失败；触及临时上限的类别还用该上限作删失下界，不只拟合成功任务。
        bounded = dict(observed)
        for s in cohort:
            if s["result"]["termination"] in ("budget-exhausted", "timeout"):
                for field in ("seconds", "tokens", "provider_calls", "tool_calls"):
                    bounded[field] = max(bounded[field], s["limits"][field])
        limits = {f: max(1, math.ceil(v * 1.5)) for f, v in bounded.items()}
        limits["tokens"] = max(limits["tokens"], limits["single_request_tokens"])
        limits["cleanup_seconds"] = max(20, limits["cleanup_seconds"])
        categories[category] = {
            "observed_max": observed,
            "limits": limits,
            "evidence_runs": [s["result"]["run_id"] for s in cohort],
            "censored_runs": [
                s["result"]["run_id"]
                for s in cohort
                if s["result"]["termination"] in ("budget-exhausted", "timeout")
            ],
            "planned_frozen_tasks": count,
        }
    total = {
        field: math.ceil(
            sum(categories[c]["limits"][field] * n * 9 for c, n in FROZEN_COUNTS.items()) * 1.1
        )
        for field in ("seconds", "tokens", "provider_calls", "tool_calls")
    }
    total["single_request_tokens"] = max(
        c["limits"]["single_request_tokens"] for c in categories.values()
    )
    total["cleanup_seconds"] = max(c["limits"]["cleanup_seconds"] for c in categories.values())
    return {
        "schema_version": 1,
        "state": "CALIBRATED_NOT_FROZEN",
        "categories": categories,
        "total": total,
        "margin": "类别最大值/预算删失下界乘1.5，总计划预留再乘1.1",
        "formal_model_calls": 0,
        "requires_stage10_identity_validation": True,
    }


def review(root: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    identity = json.loads((root / "identity.json").read_text())
    save(output / "runtime-audit.json", runtime_audit(root, identity))
    plan = json.loads((root / "plan.json").read_text())
    tasks = {
        t.task_id: t
        for source in ("live", "specialty")
        for p in (REPO / "evaluation" / source / "assets/contracts").glob("*.json")
        for t in [EvaluationTask.from_json(p.read_text())]
    }
    samples = []
    for row in plan:
        run = root / "runs" / row["run_id"]
        data = run_metrics(run)
        require(data["started"] and data["metrics"] is not None, "先导缺少已开始真实运行")
        require(data["result"]["task_sha256"] == identity["tasks"][row["task_id"]], "任务身份错配")
        require(
            data["metrics"]["provider_calls"] > 0 and not data["metrics"]["truncated_tail"],
            "轨迹不足",
        )
        task = tasks[row["task_id"]]
        records, _ = read_records(run / "worker/ledger.jsonl")
        ends = [r["data"]["usage"]["raw"] for r in records if r["kind"] == "request_end"]
        fields = (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
        data.pop("raw_token_sums", None)
        data["raw_token_lower_bounds"] = {
            field: sum(token_count(raw.get(field)) or 0 for raw in ends) for field in fields
        }
        data["raw_field_unknown_requests"] = {
            field: sum(token_count(raw.get(field)) is None for raw in ends) for field in fields
        }
        request_bytes = [
            (run / "worker" / r["data"]["input"]["path"]).stat().st_size
            for r in records
            if r["kind"] == "request_start"
        ]
        data.update(
            source=task.source,
            budget_category=task.budget_category,
            pressure_tags=task.pressure_tags,
            limits=row["limits"],
            outer_metrics=rebuild(run / "orchestration.jsonl"),
            max_request_bytes=max(request_bytes, default=0),
        )
        samples.append(data)
    save(output / "runs.json", samples)
    save(output / "calibrated-budgets.json", calibration(samples))
    summary = {}
    for source in ("live", "specialty"):
        for config in ("full", "no-compression", "eager-schema"):
            group = [
                s for s in samples if s["source"] == source and s["result"]["config_id"] == config
            ]
            require(len(group) == 12, "主集先导配对缺口")
            summary[f"{source}/{config}"] = {
                "runs": 12,
                "strict_success": sum(s["strict_success"] for s in group),
                "live_resolved": sum(s["result"]["live_resolved"] is True for s in group)
                if source == "live"
                else None,
                "terminations": dict(Counter(s["result"]["termination"] for s in group)),
                "known_token_lower_bound": sum(
                    s["metrics"]["known_token_lower_bound"] for s in group
                ),
                "unknown_usage_requests": sum(
                    s["metrics"]["unknown_usage_requests"] for s in group
                ),
                "provider_calls": sum(s["metrics"]["provider_calls"] for s in group),
                "tool_calls": sum(s["metrics"]["tool_calls"] for s in group),
                "discovery_calls": sum(s["metrics"]["discovery_calls"] for s in group),
                "offloads": sum(s["offloads"] for s in group),
                "summary_requests": sum(s["summary_requests"] for s in group),
                "main_end_to_end_seconds": sum(
                    s["outer_metrics"]["main_end_to_end_seconds"] for s in group
                ),
                "peak_price_usd_upper": sum(s["peak_price_usd_upper"] for s in group)
                if not any(s["unknown_cost"] for s in group)
                else None,
            }
    save(
        output / "summary.json",
        {
            "schema_version": 1,
            "state": "PILOT_MEASURED",
            "planned": 72,
            "recorded": len(samples),
            "independent_tasks": 24,
            "repeats": 1,
            "groups": summary,
            "formal_model_calls": 0,
            "response_model_is_alias": True,
        },
    )
    archives = export_archives(root, output)
    for name in ("identity.json", "policy.json", "plan.json", "probe-output.json"):
        save(output / name, json.loads((root / name).read_text()))
    save(output / "archive-manifest.json", {"files": archives, "secrets_absent": True})
    print(json.dumps({"recorded": len(samples), "archives": len(archives)}))


def export_archives(root: Path, output: Path) -> dict[str, str]:
    secrets = [
        p.api_key.encode() for p in load(str(REPO / ".novacode/config.yaml")).providers if p.api_key
    ]
    docker_cfg = Path.home() / "snap/docker/current/.docker/config.json"
    if docker_cfg.exists():
        for auth in json.loads(docker_cfg.read_text()).get("auths", {}).values():
            if auth.get("auth"):
                secrets += [
                    auth["auth"].encode(),
                    base64.b64decode(auth["auth"]).partition(b":")[2],
                ]
    archives = {}
    for run in sorted((root / "runs").iterdir()):
        if not run.is_dir():
            continue
        files = [
            p
            for p in run.rglob("*")
            if p.is_file() and not p.is_symlink() and "workspace" not in p.relative_to(run).parts
        ]
        for p in files:
            require(not any(s and s in p.read_bytes() for s in secrets), "交付证据含凭据")
        path = output / (run.name + ".tar.gz")
        with tarfile.open(path, "x:gz") as archive:
            for p in files:
                archive.add(p, arcname=p.relative_to(run))
        archives[path.name] = digest(path)
    return archives


def runtime_audit(root: Path, identity: dict[str, Any]) -> dict[str, Any]:
    require(digest(root / "runtime.tar") == identity["runtime_sha256"], "运行时归档改变")
    wheel = next((root / "wheels").glob("novacode-*.whl"))
    checked = 0
    with zipfile.ZipFile(wheel) as archive:
        for name, sha in identity["files"].items():
            require(digest(REPO / name) == sha, "运行后源码/锁/策略改变")
            if name.startswith("src/"):
                import hashlib

                require(
                    hashlib.sha256(archive.read(name.removeprefix("src/"))).hexdigest() == sha,
                    "执行wheel与交付源码不符",
                )
                checked += 1
        require(
            not any(
                n.endswith(("row.json", "acceptance.json", "definitions.py"))
                for n in archive.namelist()
            ),
            "wheel混入隐藏验收资产",
        )
    return {
        "source_files_checked": checked,
        "wheel_sha256": digest(wheel),
        "runtime_sha256": identity["runtime_sha256"],
        "source_matches_wheel": True,
        "hidden_assets_in_wheel": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review(args.root.resolve(), args.output.resolve())
