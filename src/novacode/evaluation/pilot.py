"""开发先导的完整准入、配对顺序与保留失败的只读汇总。"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from novacode.evaluation.budget import BudgetLimits
from novacode.evaluation.contracts import CONFIGS, EvaluationTask, require
from novacode.evaluation.ledger import read_records, rebuild
from novacode.evaluation.usage import count


def limits_for(policy: dict[str, Any], task: EvaluationTask) -> BudgetLimits:
    name = "long_context" if task.category == "long-context" else "temporary"
    return BudgetLimits(**policy[name])


def validate_policy(policy: dict[str, Any], tasks: tuple[EvaluationTask, ...]) -> None:
    require(policy["schema_version"] == 1 and policy["concurrency"] == 1, "先导身份不完整")
    require(type(policy["seed"]) is int, "缺少顺序种子")
    require(policy["requested_model"] == "deepseek-v4-flash", "请求模型变化")
    require(policy["served_model"] == "DeepSeek-V4.1-Flash", "缺少获授权的服务模型身份")
    require(policy["endpoint"] == "https://api.deepseek.com/anthropic", "端点变化")
    require(bool(policy["model_identity_source"]) and bool(policy["instructions"]), "身份依据缺失")
    require(
        policy["thinking"] is True
        and type(policy["sdk_max_retries"]) is int
        and policy["sdk_max_retries"] == 0,
        "模型参数未齐备",
    )
    for name in ("context_window", "endpoint_context_tokens", "max_output_tokens"):
        require(type(policy[name]) is int and policy[name] > 0, f"缺少数值: {name}")
    require(33000 < policy["context_window"] <= policy["endpoint_context_tokens"], "窗口不合法")
    timeout = policy["timeout_seconds"]
    require(
        type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0, "缺少有限请求超时"
    )
    total = BudgetLimits(**policy["total"])
    probe = BudgetLimits(**policy["probe"])
    require(probe.provider_calls == 3, "用量探针必须有三次请求预算")
    require(
        len(tasks) == 24
        and len({t.task_id for t in tasks}) == 24
        and all(t.suite == "development" for t in tasks),
        "只允许24道独立开发题",
    )
    from novacode.evaluation.corpus import quota_report, validate_corpus

    validate_corpus(tasks)
    report = quota_report(tasks, {t.task_id: "ADMITTED" for t in tasks})
    require(
        all(
            row["gap"] == 0
            and not any(row["difficulty_gaps"].values())
            and not any(row["category_gaps"].values())
            for row in report["rows"]
            if row["suite"] == "development"
        ),
        "开发配额不足",
    )
    # 三组72次与单题三组tmux另账，整次预留不因失败或未知usage退还。
    reserved = [probe] + [limits_for(policy, t) for t in tasks for _ in CONFIGS]
    representative = next(t for t in tasks if t.task_id == "S01")
    reserved += [limits_for(policy, representative)] * 3
    for field in ("seconds", "tokens", "provider_calls", "tool_calls"):
        require(
            sum(getattr(x, field) for x in reserved) <= getattr(total, field),
            f"先导总预算不足: {field}",
        )


def plan_runs(policy: dict[str, Any], tasks: tuple[EvaluationTask, ...]) -> list[dict[str, Any]]:
    validate_policy(policy, tasks)
    orders = tuple(itertools.permutations(CONFIGS))
    ordered = sorted(
        tasks, key=lambda t: hashlib.sha256(f"{policy['seed']}:{t.task_id}".encode()).hexdigest()
    )
    ordered.sort(key=lambda t: {"S01": 0, "L02": 1}.get(t.task_id, 2))
    return [
        {
            "run_id": f"{task.task_id}-{config}",
            "task_id": task.task_id,
            "config_id": config,
            "repeat_id": 1,
            "pair_id": task.task_id,
            "state": "PLANNED",
            "limits": asdict(limits_for(policy, task)),
        }
        for index, task in enumerate(ordered)
        for config in orders[index % 6]
    ]


def run_metrics(output: Path) -> dict[str, Any]:
    result = json.loads((output / "result.json").read_text())
    started = bool(result["termination"])
    strict = (
        result["termination"] == "completed"
        and result["acceptance"] == "passed"
        and result["cleanup"] == result["scope"] == "passed"
    )
    data: dict[str, Any] = {"result": result, "started": started, "strict_success": strict}
    path = output / "worker/ledger.jsonl"
    if not path.exists():
        return {**data, "metrics": None, "unknown_cost": True}
    records, _ = read_records(path)
    metrics = rebuild(path)
    ends = [r["data"] for r in records if r["kind"] == "request_end"]
    starts = [r["data"] for r in records if r["kind"] == "request_start"]
    prep = [r["data"] for r in records if r["kind"] == "context_preparation"]
    models = sorted({e.get("response", {}).get("model", "unknown") for e in ends})
    raw_fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    raw = {f: sum(count(e["usage"]["raw"].get(f)) or 0 for e in ends) for f in raw_fields}
    missing = {f: sum(count(e["usage"]["raw"].get(f)) is None for e in ends) for f in raw_fields}
    complete = metrics["unknown_usage_requests"] == 0 and len(ends) == len(starts)
    # 该值是当日公开峰值价上界，不冒充账单或把未知费用写成零。
    price = (
        (
            raw["input_tokens"] * 0.3
            + raw["cache_read_input_tokens"] * 0.006
            + raw["output_tokens"] * 1.2
        )
        / 1_000_000
        if complete and not raw["cache_creation_input_tokens"]
        else None
    )
    return {
        **data,
        "metrics": metrics,
        "response_models": models,
        "raw_token_lower_bounds": raw,
        "raw_field_unknown_requests": missing,
        "peak_price_usd_upper": price,
        "unknown_cost": price is None,
        "offloads": sum(p["offloaded"] for p in prep),
        "summary_requests": sum(s["role"] == "summary" for s in starts),
        "summaries_accepted": sum(p["accepted"] for p in prep),
        "thinking_sent_requests": sum(s["parameters"]["thinking_sent"] for s in starts),
        "max_input_estimate": max((s["input_estimate"] for s in starts), default=0),
        "max_request_tokens": max((e["usage"]["known_lower_bound"] for e in ends), default=0),
        "budget_stops": [r["data"] for r in records if r["kind"] == "budget_stop"],
    }
