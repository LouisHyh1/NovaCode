"""校验公开资产与外部验收资产；不解包、不运行目标命令。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from novacode.evaluation.contracts import (
    Asset,
    EvaluationCampaign,
    EvaluationTask,
    fingerprint,
    object_data,
    require,
)


def verify_asset(asset: Asset, root: Path) -> Path:
    base = root.resolve(strict=True)
    path = (base / asset.path).resolve(strict=True)
    require(path.is_relative_to(base) and path.is_file(), f"资产越界或不是文件: {asset.path}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    require(actual == asset.sha256, f"资产指纹不符: {asset.path}")
    return path


def public_requests(task: EvaluationTask, public_root: Path) -> tuple[str, ...]:
    path = verify_asset(task.public_input, public_root)
    data = object_data(path.read_text(encoding="utf-8"))
    require(
        isinstance(data, dict) and set(data) == {"schema_version", "requests"},
        "公开输入只能包含 schema_version 和 requests，不得混入答案或判题字段",
    )
    require(type(data["schema_version"]) is int and data["schema_version"] == 1, "无效公开输入版本")
    requests = data["requests"]
    require(
        isinstance(requests, list)
        and bool(requests)
        and all(isinstance(item, str) and item.strip() for item in requests),
        "固定请求脚本不完整",
    )
    require(task.source != "live" or len(requests) == 1, "Live 必须为原始单请求 rollout")
    return tuple(requests)


def validate_task_assets(
    task: EvaluationTask,
    public_root: Path,
    acceptance_root: Path,
    budget_categories: frozenset[str],
) -> tuple[str, ...]:
    public = public_root.resolve(strict=True)
    hidden = acceptance_root.resolve(strict=True)
    require(
        not public.is_relative_to(hidden) and not hidden.is_relative_to(public),
        "公开根与外部验收根必须分离",
    )
    require(task.budget_category in budget_categories, "预算类别未定义")
    verify_asset(task.initial_state, public)
    verify_asset(task.environment, public)
    for asset in task.acceptance_assets:
        path = verify_asset(asset, hidden)
        require(not path.is_relative_to(public), "验收资产不得进入公开环境")
    return public_requests(task, public)


def validate_campaign_tasks(
    campaign: EvaluationCampaign,
    tasks: tuple[EvaluationTask, ...],
    public_root: Path,
    acceptance_root: Path,
    budget_categories: frozenset[str],
) -> None:
    by_id = {task.task_id: task for task in tasks}
    require(
        len(by_id) == len(tasks) and set(by_id) == set(campaign.task_ids), "Campaign 任务集合不一致"
    )
    scripts = {}
    for task in tasks:
        require(task.suite == campaign.suite, "Campaign 不得混用开发和冻结任务")
        scripts[task.task_id] = validate_task_assets(
            task, public_root, acceptance_root, budget_categories
        )
    for run in campaign.runs:
        task = by_id[run.task_id]
        require(
            run.task_sha256 == fingerprint(task)
            and run.initial_sha256 == task.initial_state.sha256,
            "运行引用的任务或初始状态指纹不一致",
        )
        require(len(run.agent_run_ids) <= len(scripts[task.task_id]), "Agent Run 数量超过固定脚本")
        if run.termination == "completed":
            require(
                run.completed_requests == len(scripts[task.task_id]), "正常完成不能省略脚本请求"
            )
        require(task.source == "live" or run.live_resolved is None, "专项没有官方 Live resolved")
