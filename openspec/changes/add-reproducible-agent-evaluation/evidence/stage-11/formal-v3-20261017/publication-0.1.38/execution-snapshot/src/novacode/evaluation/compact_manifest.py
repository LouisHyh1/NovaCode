"""v3 清单的元数据选择、预算与授权边界；旧清单保持旧规模。"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

from novacode.evaluation.compact import PARENT_SHA, selection
from novacode.evaluation.contracts import EvaluationTask, require
from novacode.evaluation.frozen import digest, identity


def compact_budgets(parent: dict[str, Any], tasks: tuple[EvaluationTask, ...]) -> dict[str, Any]:
    budgets = copy.deepcopy(parent)
    counts = Counter(t.budget_category for t in tasks)
    for category, value in budgets["categories"].items():
        value["planned_frozen_tasks"] = counts[category]
    for field in ("seconds", "tokens", "provider_calls", "tool_calls"):
        total = sum(counts[k] * 9 * v["limits"][field] for k, v in budgets["categories"].items())
        budgets["total"][field] = (total * 11 + 9) // 10
    require(
        budgets["total"]
        == {
            "seconds": 66231,
            "tokens": 176301111,
            "provider_calls": 7317,
            "tool_calls": 13187,
            "single_request_tokens": 508821,
            "cleanup_seconds": 55,
        },
        "精简预算与协议不一致",
    )
    return budgets


def subset(corpus: dict[str, Any], selected: set[str]) -> dict[str, Any]:
    return {
        **corpus,
        "tasks": {k: v for k, v in corpus["tasks"].items() if k in selected},
        "pressure_subsets": {
            tag: [task for task in ids if task in selected]
            for tag, ids in corpus["pressure_subsets"].items()
            if any(t in selected for t in ids)
        },
    }


def coordinator_files(repo: Path) -> dict[str, str]:
    return {
        p.relative_to(repo).as_posix(): digest(p)
        for p in sorted((repo / "evaluation/formal").glob("*.py"))
    }


def verify_compact(
    data: dict[str, Any], repo: Path, pool: tuple[EvaluationTask, ...], corpus: dict[str, Any]
) -> tuple[tuple[EvaluationTask, ...], dict[str, Any]]:
    parent_path = repo / data["parent_manifest"]["path"]
    require(
        digest(parent_path) == data["parent_manifest"]["sha256"] == PARENT_SHA,
        "父 manifest 指纹变化",
    )
    parent = json.loads(parent_path.read_text())
    require(corpus == parent["corpus"], "父资产或资格证据变化")
    from dataclasses import asdict

    chosen = selection([asdict(t) for t in pool], PARENT_SHA)
    require(chosen == data["selection"], "固定元数据抽样变化")
    selected = {task for ids in chosen["selected"].values() for task in ids}
    tasks = tuple(t for t in pool if t.task_id in selected)
    require(compact_budgets(parent["budgets"], tasks) == data["budgets"], "v3 类别上限或总预留变化")
    require(coordinator_files(repo) == data["coordinator_files"], "协调器/归档/报告身份变化")
    require(
        data["policy"]["max_iterations"] == 25
        and data["policy"]["iteration_scope"] == "per-agent-run",
        "迭代身份未固定",
    )
    require(data["baseline_contract"] == "typed-git-initial-v2", "类型化基线合同变化")
    require(set(data["baselines"]) == selected, "初始基线未覆盖全部选中任务")
    for task_id, baseline in data["baselines"].items():
        require(digest(Path(baseline["path"])) == baseline["sha256"], "可信初始基线指纹变化")
        require(
            baseline["task_id"] == task_id and baseline["schema_version"] == 2, "初始基线身份错配"
        )
        info = json.loads(Path(baseline["path"]).read_text())
        task = next(t for t in tasks if t.task_id == task_id)
        require(
            info["schema_version"] == 2
            and info["base_commit"] == task.base_commit
            and info["tree"] == baseline["tree"]
            and len(info["entries"]) == baseline["entries"],
            "初始基线内容或树身份错配",
        )
        require(
            data["external_files"].get(baseline["archive_path"]) == baseline["archive_sha256"],
            "初始对象归档未绑定冻结身份",
        )
    for gate in ("history_gate", "chain_gate", "deployment_gate", "storage_gate"):
        require(digest(repo / data[gate]["path"]) == data[gate]["sha256"], "前置证据指纹变化")
    require(
        json.loads((repo / data["history_gate"]["path"]).read_text())["state"] == "HISTORY_AUDITED",
        "历史审计未通过",
    )
    require(
        json.loads((repo / data["chain_gate"]["path"]).read_text())["state"]
        == "EVIDENCE_CHAIN_VALID",
        "独立链路未通过",
    )
    require(
        data["disk_admission"]["minimum_free_bytes"] > 0
        and data["disk_admission"]["measured_peak_bytes"] > 0,
        "缺少实测磁盘门槛",
    )
    storage = json.loads((repo / data["storage_gate"]["path"]).read_text())
    require(
        storage["state"] == "STORAGE_MEASURED"
        and storage["peak_increment_bytes"] == data["disk_admission"]["measured_peak_bytes"],
        "存储峰值未经真实测量",
    )
    return tasks, subset(corpus, selected)


def validate_authorization(data: dict[str, Any], authorization: Any, manifest_sha: str) -> None:
    require(isinstance(authorization, dict), "新冻结身份及预算尚未获用户明确确认，禁止模型调用")
    require(
        authorization.get("approved") is True
        and authorization.get("campaign_id") == data["campaign_id"]
        and authorization.get("manifest_sha256") == manifest_sha
        and authorization.get("budget_sha256") == identity(data["budgets"]["total"]),
        "启动确认未绑定当前冻结身份和预算",
    )


def check_disk(root: Path, data: dict[str, Any]) -> None:
    import shutil

    require(
        shutil.disk_usage(root).free >= data["disk_admission"]["minimum_free_bytes"],
        "磁盘低于冻结准入门槛；保留历史和失败现场，停止新任务",
    )
