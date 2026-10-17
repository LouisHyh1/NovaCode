"""从不可覆盖的原始账本重建分来源报告和任务级配对区间。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from evaluation.formal.run import MANIFEST, save, snapshot  # noqa: E402
from novacode.evaluation.contracts import CONFIGS, EvaluationRun, require  # noqa: E402
from novacode.evaluation.frozen import digest, load_tasks  # noqa: E402
from novacode.evaluation.ledger import read_records  # noqa: E402


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean": statistics.mean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "p90": ordered[math.ceil(0.9 * len(values)) - 1] if values else None,
    }


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def paired_bootstrap(
    differences: dict[str, float], *, seed: int, resamples: int = 10000
) -> dict[str, Any]:
    require(resamples > 0, "重采样次数无效")
    values = [differences[key] for key in sorted(differences)]
    n = len(values)
    result: dict[str, Any] = {
        "independent_tasks": n,
        "point": statistics.mean(values) if n else None,
        "confidence": 0.95,
        "ci": None,
        "resamples": resamples,
        "seed": seed,
        "degenerate": n < 2 or max(values) - min(values) < 1e-12,
        "reliable": n >= 2 and max(values) - min(values) >= 1e-12,
        "conditional_on_selected_tasks": True,
    }
    if n < 2:
        return result
    rng = random.Random(seed)
    draws = [sum(rng.choices(values, k=n)) / n for _ in range(resamples)]
    result["ci"] = [percentile(draws, 0.025), percentile(draws, 0.975)]
    return result


def metric(sample: dict[str, Any], name: str) -> float | None:
    if name == "strict_percentage_points":
        run = EvaluationRun.from_json(json.dumps(sample["result"]))
        return float(run.strict_success) * 100
    metrics = sample.get("metrics") or {}
    if name == "measured_tokens" and not sample["cost_complete"]:
        return None
    value = metrics.get(name)
    return float(value) if value is not None else None


def compare(
    samples: list[dict[str, Any]], tasks: list[str], other: str, name: str, seed: int
) -> dict[str, Any]:
    differences = {}
    for task in tasks:
        pairs = {
            (s["result"]["config_id"], s["result"]["repeat_id"]): metric(s, name)
            for s in samples
            if s["result"]["task_id"] == task
        }
        keys = [(config, repeat) for config in ("full", other) for repeat in (1, 2, 3)]
        if any(pairs.get(key) is None for key in keys):
            continue
        differences[task] = (
            sum(
                cast(float, pairs[("full", repeat)]) - cast(float, pairs[(other, repeat)])
                for repeat in (1, 2, 3)
            )
            / 3
        )
    return {
        "comparison": ["full", other],
        "metric": name,
        "planned_tasks": len(tasks),
        "omitted_tasks": sorted(set(tasks) - differences.keys()),
        **paired_bootstrap(differences, seed=seed),
    }


def verify_archive(output: Path) -> None:
    receipt = json.loads((output / "archive.json").read_text())
    archive = Path(receipt["path"])
    require(digest(archive) == receipt["sha256"], "原始归档变化")
    with tarfile.open(archive) as raw:
        require(set(raw.getnames()) == set(receipt["files"]), "原始产物集合变化")
        for name, sha in receipt["files"].items():
            stream = raw.extractfile(name)
            require(stream is not None, "原始产物缺失")
            assert stream is not None
            require(hashlib.sha256(stream.read()).hexdigest() == sha, "归档成员变化")
    for name, sha in receipt["files"].items():
        path = output / name
        if path.exists():
            require(digest(path) == sha, "工作副本与原始归档不一致")


def collect(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = {row["run_id"] for row in manifest["plan"]}
    for path in (root / "runs").glob("*/result.json"):
        recorded = json.loads(path.read_text())
        require(
            recorded["campaign_id"] == manifest["campaign_id"] and recorded["run_id"] in allowed,
            "报告拒绝跨 campaign 或计划外结果",
        )
    samples = []
    for row in manifest["plan"]:
        candidates = sorted((root / "runs").glob(row["run_id"] + "*"))
        started = []
        for output in candidates:
            if not (output / "result.json").exists():
                raise ValueError("中断结果缺失，须先保留并审计")
            sample = snapshot(output)
            if sample["result"]["termination"] is None:
                continue
            verify_archive(output)
            require(sample == json.loads((output / "sample.json").read_text()), "增量重建不一致")
            result = sample["result"]
            for key in (
                "run_id",
                "campaign_id",
                "task_id",
                "config_id",
                "repeat_id",
                "task_sha256",
            ):
                require(result[key] == row[key], "原计划结果身份变化")
            sample["sequence"] = row["sequence"]
            sample["evidence"] = output.name
            started.append(sample)
        require(len(started) <= 1, "原计划重复执行")
        samples.extend(started)
    return samples


def summarize(samples: list[dict[str, Any]], planned: int) -> dict[str, Any]:
    runs = [EvaluationRun.from_json(json.dumps(s["result"])) for s in samples]
    success = sum(r.strict_success for r in runs)
    n = len(runs)
    measured = [s for s in samples if s["cost_complete"]]
    metrics = [s["metrics"] for s in samples if s["metrics"] is not None]
    return {
        "planned": planned,
        "started": n,
        "recorded": sum(r.state == "RECORDED" for r in runs),
        "completed": sum(r.termination == "completed" for r in runs),
        "strict_successes": success,
        "strict_failures": n - success,
        "unstarted": planned - n,
        "strict_rate_started": success / n if n else None,
        "planned_rate_bounds": [success / planned, (success + planned - n) / planned],
        "live_resolved": sum(r.live_resolved is True for r in runs),
        "live_resolved_unknown": sum(r.live_resolved is None for r in runs),
        "termination": dict(Counter(r.termination for r in runs)),
        "acceptance": dict(Counter(r.acceptance for r in runs)),
        "scope": dict(Counter(r.scope for r in runs)),
        "cleanup": dict(Counter(r.cleanup for r in runs)),
        "cost_complete_runs": len(measured),
        "cost_complete_ratio": len(measured) / n if n else None,
        "token_lower_bound": sum(m["known_token_lower_bound"] for m in metrics),
        "unknown_usage_requests": sum(m["unknown_usage_requests"] for m in metrics),
        "metrics_missing_runs": n - len(metrics),
        "monetary_cost": None,
        "distributions": {
            name: distribution([v for s in samples if (v := metric(s, name)) is not None])
            for name in (
                "measured_tokens",
                "known_token_lower_bound",
                "provider_calls",
                "tool_calls",
                "main_end_to_end_seconds",
            )
        },
        "mechanisms": {
            name: sum((s.get(name) or 0) for s in samples)
            for name in ("offloads", "summary_requests", "summaries_accepted")
        },
        "tools": {
            name: sum(m[name] for m in metrics)
            for name in (
                "tool_calls",
                "tool_executed",
                "tool_succeeded",
                "tool_denied",
                "tool_errors",
                "tool_retries",
                "discovery_calls",
                "fault_injections",
                "incomplete_tools",
            )
        },
        "response_models": sorted({m for s in samples for m in s.get("response_models", [])}),
    }


def failure_audit(root: Path, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """初步分类引用原评分与事件；不以工具首次失败代替最终失败原因。"""
    rows = []
    for sample in samples:
        result = sample["result"]
        if EvaluationRun.from_json(json.dumps(result)).strict_success:
            continue
        folder = root / "runs" / sample["evidence"]
        judge = folder / "judge.json"
        conditions = json.loads(judge.read_text()).get("conditions", {}) if judge.exists() else {}
        causes = ["acceptance/" + key for key, passed in conditions.items() if not passed]
        if result["termination"] != "completed":
            causes.insert(0, "termination/" + result["termination"])
        for field in ("scope", "cleanup"):
            if result[field] != "passed":
                causes.append(field + "/" + result[field])
        if result["acceptance"] == "ungradable":
            causes.append("acceptance/ungradable")
        ledger = folder / "worker/ledger.jsonl"
        records = read_records(ledger)[0] if ledger.exists() else []
        rows.append(
            {
                "run_id": result["run_id"],
                "automatic_causes": causes,
                "stop_notices": [
                    r["data"] for r in records if r["kind"] in ("agent_notice", "budget_stop")
                ],
                "not_manually_regraded": True,
                "evidence": {
                    name: digest(folder / name)
                    for name in (
                        "result.json",
                        "orchestration.jsonl",
                        "worker/ledger.jsonl",
                        "judge.json",
                    )
                    if (folder / name).exists()
                },
                "tool_errors_recovered_or_secondary": (sample.get("metrics") or {}).get(
                    "tool_errors"
                ),
            }
        )
    return rows


def report(root: Path, output: Path, manifest_path: Path = MANIFEST) -> None:
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(manifest_path.read_text())
    require(
        digest(manifest_path)
        == json.loads((root / "identity.json").read_text())["manifest_sha256"],
        "清单锚点变化",
    )
    tasks = [
        t
        for source in ("live", "specialty")
        for t in load_tasks(REPO / manifest["asset_roots"][source])
        if t.task_id in manifest["corpus"]["tasks"]
    ]
    planned = len(manifest["plan"])
    samples = collect(root, manifest)
    save(output / "runs.json", samples)
    save(output / "failure-audit.json", failure_audit(root, samples))
    save(
        output / "summary.json",
        {
            "schema_version": 1,
            "campaign_id": manifest["campaign_id"],
            "state": "FORMAL_COMPLETE" if len(samples) == planned else "PARTIAL",
            "planned": planned,
            "recorded": len(samples),
            "unstarted": planned - len(samples),
            "sources": {
                source: {
                    config: summarize(
                        [
                            s
                            for s in samples
                            if s["result"]["task_id"]
                            in {t.task_id for t in tasks if t.source == source}
                            and s["result"]["config_id"] == config
                        ],
                        sum(
                            r["config_id"] == config
                            and r["task_id"] in {t.task_id for t in tasks if t.source == source}
                            for r in manifest["plan"]
                        ),
                    )
                    for config in CONFIGS
                }
                for source in ("live", "specialty")
            },
            "selection": {
                source: {
                    "tasks": sum(t.source == source for t in tasks),
                    "repositories": sorted({t.repository for t in tasks if t.source == source}),
                    "families": sorted({t.family_id for t in tasks if t.source == source}),
                }
                for source in ("live", "specialty")
            },
            "interpretation": "所选题库条件分析；重复不是独立任务，非官方完整榜单，无交互估计；"
            "父题库历史结果已被查看，非全新未接触留出集",
        },
    )
    cohorts = {
        source: [t.task_id for t in tasks if t.source == source] for source in ("live", "specialty")
    }
    cohorts.update(
        {"pressure/" + tag: ids for tag, ids in manifest["corpus"]["pressure_subsets"].items()}
    )
    save(
        output / "paired.json",
        {
            cohort: [
                compare(samples, members, other, name, manifest["policy"]["analysis"]["seed"])
                for other in ("no-compression", "eager-schema")
                for name in (
                    "strict_percentage_points",
                    "measured_tokens",
                    "main_end_to_end_seconds",
                    "tool_calls",
                )
            ]
            for cohort, members in cohorts.items()
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args()
    report(args.root.resolve(), args.output.resolve(), args.manifest.resolve())
