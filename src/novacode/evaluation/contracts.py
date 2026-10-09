"""版本化评测身份与结果，使用标准库 JSON 保存。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
SOURCES = ("live", "specialty")
SUITES = ("development", "frozen")
DIFFICULTIES = ("easy", "medium", "hard")
SPECIALTIES = ("localization", "test-addition", "tool-failure", "long-context", "tool-discovery")
CONFIGS = ("full", "no-compression", "eager-schema")
TERMINATIONS = (
    "completed",
    "timeout",
    "budget-exhausted",
    "cancelled",
    "context-overflow",
    "provider-error",
    "runner-error",
    "infrastructure-error",
    "cleanup-error",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def text(value: str, name: str) -> None:
    require(isinstance(value, str) and bool(value.strip()), f"{name} 不能为空")


def digest(value: str) -> None:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "必须提供小写 SHA-256 指纹",
    )


def strings(values: tuple[str, ...], name: str, *, empty: bool = False) -> None:
    require(isinstance(values, tuple), f"{name} 必须为元组")
    require(empty or bool(values), f"{name} 不能为空")
    for value in values:
        text(value, name)
    require(len(set(values)) == len(values), f"{name} 包含重复值")


def relative_path(value: str) -> None:
    text(value, "资产路径")
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value
        and ":" not in value
        and path.as_posix() == value
        and value != ".",
        f"路径必须为规范的相对 POSIX 路径: {value}",
    )


def version(value: int) -> None:
    require(type(value) is int and value == SCHEMA_VERSION, "不支持的 schema_version")


def json_text(value: Any) -> str:
    return json.dumps(asdict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def object_data(raw: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in result, f"重复 JSON 字段: {key}")
            result[key] = value
        return result

    data = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(data, dict), "合同必须为 JSON 对象")
    require("schema_version" in data, "缺少 schema_version")
    return dict(data)


def tuple_fields(data: dict[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        if name in data:
            require(isinstance(data[name], list), f"{name} 必须为 JSON 数组")
            data[name] = tuple(data[name])


@dataclass(frozen=True, slots=True)
class Asset:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        relative_path(self.path)
        digest(self.sha256)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationTask:
    task_id: str
    candidate_id: str
    source: str
    suite: str
    family_id: str
    problem_keys: tuple[str, ...]
    repository: str
    source_revision: str
    base_commit: str
    difficulty: str
    category: str
    pressure_tags: tuple[str, ...]
    budget_category: str
    initial_state: Asset
    public_input: Asset
    environment: Asset
    acceptance_assets: tuple[Asset, ...]
    acceptance_conditions: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    builder_id: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        version(self.schema_version)
        for name in (
            "task_id",
            "candidate_id",
            "family_id",
            "repository",
            "source_revision",
            "budget_category",
        ):
            text(getattr(self, name), name)
        require(self.source in SOURCES and self.suite in SUITES, "无效来源或题集")
        require(self.difficulty in DIFFICULTIES, "无效难度")
        require(
            self.category == "issue-fix" if self.source == "live" else self.category in SPECIALTIES,
            "主类别与来源不符",
        )
        require(re.fullmatch(r"[0-9a-f]{40}", self.base_commit) is not None, "base_commit 未固定")
        strings(self.problem_keys, "problem_keys")
        for key in self.problem_keys:
            require(
                re.fullmatch(r"(?:issue|fix|defect):\S+", key) is not None,
                "问题身份必须为 issue/fix/defect 的规范键",
            )
        if self.source == "live":
            require(
                re.fullmatch(r"[0-9a-f]{40}", self.source_revision) is not None,
                "Live 数据 revision 未固定",
            )
            require(
                any(
                    re.fullmatch(r"issue:" + re.escape(self.repository) + r"#[1-9][0-9]*", key)
                    for key in self.problem_keys
                ),
                "Live 必须记录仓库及 issue 身份",
            )
        else:
            require(
                any(key.startswith("defect:") for key in self.problem_keys),
                "专项必须记录受控缺陷或业务事实身份",
            )
        strings(self.pressure_tags, "pressure_tags", empty=True)
        strings(self.allowed_paths, "allowed_paths", empty=True)
        for path in self.allowed_paths:
            relative_path(path)
            require(
                path != ".novacode" and not path.startswith(".novacode/"),
                "内部状态不属于业务修改范围",
            )
        strings(self.acceptance_conditions, "acceptance_conditions")
        require(
            isinstance(self.acceptance_assets, tuple) and bool(self.acceptance_assets),
            "缺少独立验收资产",
        )
        require(
            all(
                isinstance(asset, Asset)
                for asset in (
                    self.initial_state,
                    self.public_input,
                    self.environment,
                    *self.acceptance_assets,
                )
            ),
            "无效资产合同",
        )
        require(
            len({asset.path for asset in self.acceptance_assets}) == len(self.acceptance_assets),
            "验收资产路径重复",
        )

    @classmethod
    def from_json(cls, raw: str) -> EvaluationTask:
        data = object_data(raw)
        tuple_fields(
            data, ("problem_keys", "pressure_tags", "acceptance_conditions", "allowed_paths")
        )
        for name in ("initial_state", "public_input", "environment"):
            data[name] = Asset(**data[name])
        data["acceptance_assets"] = tuple(Asset(**entry) for entry in data["acceptance_assets"])
        return cls(**data)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationRun:
    run_id: str
    campaign_id: str
    task_id: str
    task_sha256: str
    initial_sha256: str
    config_id: str
    repeat_id: int
    state: str = "PLANNED"
    agent_run_ids: tuple[str, ...] = ()
    completed_requests: int = 0
    termination: str | None = None
    acceptance: str = "pending"
    scope: str = "pending"
    cleanup: str = "pending"
    live_resolved: bool | None = None
    block_reason: str = ""
    retry_of: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        version(self.schema_version)
        for name in ("run_id", "campaign_id", "task_id"):
            text(getattr(self, name), name)
        digest(self.task_sha256)
        digest(self.initial_sha256)
        require(self.config_id in CONFIGS, "无效配置身份")
        require(type(self.repeat_id) is int and self.repeat_id > 0, "repeat_id 必须为正整数")
        require(
            self.state in ("PLANNED", "PREFLIGHT", "RUNNING", "STOPPING", "GRADING", "RECORDED"),
            "无效运行状态",
        )
        strings(self.agent_run_ids, "agent_run_ids", empty=True)
        require(
            type(self.completed_requests) is int
            and 0 <= self.completed_requests <= len(self.agent_run_ids),
            "无效请求完成数",
        )
        require(self.termination is None or self.termination in TERMINATIONS, "无效终止原因")
        require(self.acceptance in ("pending", "passed", "failed", "ungradable"), "无效验收状态")
        require(self.scope in ("pending", "passed", "failed", "unknown"), "无效修改范围状态")
        require(self.cleanup in ("pending", "passed", "failed"), "无效清理状态")
        require(
            self.live_resolved is None or type(self.live_resolved) is bool,
            "resolved 必须为布尔或未知",
        )
        if self.state in ("PLANNED", "PREFLIGHT"):
            require(
                not self.agent_run_ids
                and self.termination is None
                and (self.acceptance, self.scope, self.cleanup) == ("pending",) * 3
                and self.live_resolved is None,
                "未开始计划不能含运行结果",
            )
        if self.state == "RECORDED":
            require(
                self.termination is not None
                and self.acceptance != "pending"
                and self.scope != "pending"
                and self.cleanup != "pending",
                "终态记录不完整",
            )
        if self.termination == "completed":
            require(
                bool(self.agent_run_ids) and self.completed_requests == len(self.agent_run_ids),
                "正常完成必须完成全部已执行请求",
            )
        if self.retry_of is not None:
            text(self.retry_of, "retry_of")
            require(self.retry_of != self.run_id, "补跑不能引用自身")

    @property
    def strict_success(self) -> bool:
        return (
            self.state == "RECORDED"
            and self.termination == "completed"
            and self.acceptance == self.scope == self.cleanup == "passed"
        )

    @property
    def plan_key(self) -> tuple[str, str, str, int]:
        return self.campaign_id, self.task_id, self.config_id, self.repeat_id

    @classmethod
    def from_json(cls, raw: str) -> EvaluationRun:
        data = object_data(raw)
        tuple_fields(data, ("agent_run_ids",))
        return cls(**data)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationCampaign:
    campaign_id: str
    suite: str
    task_ids: tuple[str, ...]
    config_ids: tuple[str, ...]
    protocol_sha256: str
    source_sha256: str
    dependency_sha256: str
    task_manifest_sha256: str
    environment_sha256: str
    parameters_sha256: str
    budget_sha256: str
    analysis_sha256: str
    seed: int
    concurrency: int
    runs: tuple[EvaluationRun, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        version(self.schema_version)
        text(self.campaign_id, "campaign_id")
        require(self.suite in SUITES, "无效题集")
        strings(self.task_ids, "task_ids")
        strings(self.config_ids, "config_ids")
        require(set(self.config_ids) <= set(CONFIGS), "无效配置集合")
        for name in (
            "protocol",
            "source",
            "dependency",
            "task_manifest",
            "environment",
            "parameters",
            "budget",
            "analysis",
        ):
            digest(getattr(self, name + "_sha256"))
        require(type(self.seed) is int, "seed 必须为整数")
        require(type(self.concurrency) is int and self.concurrency == 1, "主集并发度固定为 1")
        require(isinstance(self.runs, tuple), "runs 必须为元组")
        ids: set[str] = set()
        keys: set[tuple[str, str, str, int]] = set()
        for run in self.runs:
            require(run.run_id not in ids, "重复运行 ID")
            require(
                run.campaign_id == self.campaign_id
                and run.task_id in self.task_ids
                and run.config_id in self.config_ids,
                "运行不属于该 Campaign",
            )
            ids.add(run.run_id)
            if run.retry_of is None:
                require(run.plan_key not in keys, "重复原计划键")
                keys.add(run.plan_key)
        originals = {run.run_id: run for run in self.runs if run.retry_of is None}
        for run in self.runs:
            if run.retry_of is not None:
                original = originals.get(run.retry_of)
                require(
                    original is not None
                    and original.state == "RECORDED"
                    and original.plan_key == run.plan_key,
                    "补跑必须关联已记录的同一原计划",
                )

    @property
    def counts(self) -> dict[str, int]:
        return {
            "evaluation_tasks": len(self.task_ids),
            "evaluation_runs": len(self.runs),
            "agent_runs": sum(len(run.agent_run_ids) for run in self.runs),
        }

    @classmethod
    def from_json(cls, raw: str) -> EvaluationCampaign:
        data = object_data(raw)
        tuple_fields(data, ("task_ids", "config_ids"))
        data["runs"] = tuple(EvaluationRun.from_json(json.dumps(run)) for run in data["runs"])
        return cls(**data)
