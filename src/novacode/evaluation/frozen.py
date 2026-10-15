"""冻结身份、均衡原计划与正式执行前只读校验；这里不调用 Provider。"""

from __future__ import annotations

import hashlib
import itertools
import json
import platform
import random
import re
import subprocess
from collections import Counter
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any

from novacode.evaluation.assets import public_requests, validate_task_assets
from novacode.evaluation.budget import BudgetLimits
from novacode.evaluation.contracts import (
    CONFIGS,
    EvaluationCampaign,
    EvaluationRun,
    EvaluationTask,
    fingerprint,
    require,
)
from novacode.evaluation.corpus import SelectionLedger, quota_report, validate_corpus


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def source_files(repo: Path) -> dict[str, str]:
    paths = set((repo / "src").rglob("*.py"))
    for folder in ("live", "specialty", "frozen"):
        paths.update((repo / "evaluation" / folder).glob("*.py"))
    paths.update(repo / name for name in ("pyproject.toml", "uv.lock"))
    paths.update((repo / "evaluation/frozen").glob("*.json"))
    audit = repo / "evaluation/frozen/family-audit.md"
    if audit.exists():
        paths.add(audit)
    return {p.relative_to(repo).as_posix(): digest(p) for p in sorted(paths)}


def load_tasks(root: Path) -> tuple[EvaluationTask, ...]:
    return tuple(
        EvaluationTask.from_json(p.read_text()) for p in sorted((root / "contracts").glob("*.json"))
    )


def plan_runs(
    tasks: tuple[EvaluationTask, ...], budgets: dict[str, Any], campaign_id: str, seed: int
) -> list[dict[str, Any]]:
    require(len(tasks) == len({t.task_id for t in tasks}) == 48, "冻结计划需要48道独立任务")
    require(all(t.suite == "frozen" for t in tasks), "计划只能使用冻结题")
    orders = list(itertools.permutations(CONFIGS))
    rng = random.Random(seed)
    rng.shuffle(orders)
    plan: list[dict[str, Any]] = []
    for repeat in range(1, 4):
        ordered = sorted(tasks, key=lambda t: t.task_id)
        rng.shuffle(ordered)
        source_blocks: Counter[str] = Counter()
        for task in ordered:
            order = orders[source_blocks[task.source] % 6]
            source_blocks[task.source] += 1
            for config in order:
                plan.append(
                    {
                        "run_id": f"{campaign_id}-{task.task_id}-{repeat}-{config}",
                        "campaign_id": campaign_id,
                        "task_id": task.task_id,
                        "config_id": config,
                        "repeat_id": repeat,
                        "pair_key": f"{task.task_id}/{repeat}",
                        "sequence": len(plan) + 1,
                        "state": "PLANNED",
                        "task_sha256": fingerprint(task),
                        "initial_sha256": task.initial_state.sha256,
                        "limits": budgets["categories"][task.budget_category]["limits"],
                    }
                )
    return plan


def validate_policy(
    policy: dict[str, Any], budgets: dict[str, Any], tasks: tuple[EvaluationTask, ...]
) -> None:
    require(
        policy["schema_version"] == 1
        and policy["concurrency"] == 1
        and type(policy["seed"]) is int,
        "冻结调度身份无效",
    )
    expected = {
        "requested_model": "deepseek-v4-flash",
        "served_model": "DeepSeek-V4.1-Flash",
        "endpoint": "https://api.deepseek.com/anthropic",
        "protocol": "anthropic",
        "thinking": True,
        "sdk_max_retries": 0,
        "context_window": 200000,
        "max_output_tokens": 4096,
        "timeout_seconds": 90,
        "thinking_budget_tokens": 2048,
        "thinking_rule": "only-without-tool-history; endpoint-budget-ignored",
        "input_admission_rule": "utf8-bytes-conservative",
        "unsupported_seed": True,
        "sampling": "not-sent-endpoint-default",
        "remote_cache": "not-resettable",
        "remote_version": "mutable-alias-no-immutable-weight-pin",
        "permissions": {"allow": ["Write", "Edit", "Bash"], "deny": []},
        "ask": "deterministic-deny",
        "hooks": [],
        "memory": "empty-isolated",
        "session": "fresh-per-run",
        "roles": ["main"],
        "tool_parallelism": "read-only-parallel",
    }
    require(
        all(policy[k] == v and type(policy[k]) is type(v) for k, v in expected.items()),
        "冻结策略未完整固定",
    )
    require(
        bool(policy["instructions"])
        and bool(policy["model_identity_source"])
        and bool(policy["usage_rule"]["evidence_sha256"]),
        "必要执行参数缺失",
    )
    require(
        policy["usage_rule"]["cache_mode"] == "separate"
        and policy["usage_rule"]["protocol"] == "anthropic",
        "用量归一化规则变化",
    )
    require(
        policy["analysis"]
        == {
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
        "统计身份变化",
    )
    total = BudgetLimits(**budgets["total"])
    counts = Counter(t.budget_category for t in tasks)
    require(set(counts) == set(budgets["categories"]), "预算类别未覆盖题库")
    limits = {name: BudgetLimits(**data["limits"]) for name, data in budgets["categories"].items()}
    require(
        all(
            counts[name] == data["planned_frozen_tasks"]
            for name, data in budgets["categories"].items()
        ),
        "类别配额与校准依据不符",
    )
    for field in ("seconds", "tokens", "provider_calls", "tool_calls"):
        require(
            sum(counts[name] * 9 * getattr(value, field) for name, value in limits.items())
            <= getattr(total, field),
            "正式总预留不足",
        )


def campaign_contract(
    data: dict[str, Any], tasks: tuple[EvaluationTask, ...]
) -> EvaluationCampaign:
    fields = (
        "run_id",
        "campaign_id",
        "task_id",
        "task_sha256",
        "initial_sha256",
        "config_id",
        "repeat_id",
        "state",
    )
    return EvaluationCampaign(
        campaign_id=data["campaign_id"],
        suite="frozen",
        task_ids=tuple(t.task_id for t in tasks),
        config_ids=CONFIGS,
        protocol_sha256=data["protocol_sha256"],
        source_sha256=identity(data["source_files"]),
        dependency_sha256=data["source_files"]["uv.lock"],
        task_manifest_sha256=identity(data["corpus"]["tasks"]),
        environment_sha256=identity({t.task_id: t.environment.sha256 for t in tasks}),
        parameters_sha256=identity(data["policy"]),
        budget_sha256=identity(data["budgets"]),
        analysis_sha256=identity(data["policy"]["analysis"]),
        seed=data["policy"]["seed"],
        concurrency=1,
        runs=tuple(EvaluationRun(**{k: row[k] for k in fields}) for row in data["plan"]),
    )


def corpus_identity(
    repo: Path, roots: dict[str, str], ledgers: dict[str, str]
) -> tuple[tuple[EvaluationTask, ...], dict[str, Any]]:
    all_tasks: list[EvaluationTask] = []
    files = {}
    states = {}
    for source in ("live", "specialty"):
        root = repo / roots[source]
        tasks = load_tasks(root)
        categories = frozenset(t.budget_category for t in tasks)
        ledger = SelectionLedger(
            repo / ledgers[source], tasks, root / "public", root / "external", categories
        )
        current, _ = ledger.replay()
        require(
            len(tasks) == 24 and all(t.suite == "frozen" and t.source == source for t in tasks),
            "冻结来源未齐备",
        )
        require(set(current.values()) <= {"ADMITTED", "FROZEN"}, "候选未入库")
        for task in tasks:
            validate_task_assets(task, root / "public", root / "external", categories)
        # 包括资格证明和原始证据摘要，新增或删去文件也属于身份变化。
        files.update(
            {
                p.relative_to(repo).as_posix(): digest(p)
                for p in sorted(root.rglob("*"))
                if p.is_file() and not p.is_symlink()
            }
        )
        files[ledgers[source]] = digest(repo / ledgers[source])
        all_tasks.extend(tasks)
        states.update(current)
    frozen = tuple(all_tasks)
    dev = tuple(
        t
        for source in ("live", "specialty")
        for t in load_tasks(repo / "evaluation" / source / "assets")
    )
    require(len(dev) == 24, "开发集隔离依据缺失")
    validate_corpus((*dev, *frozen))
    require(len({t.family_id for t in frozen}) == 48, "冻结集独立问题家族重复")
    report = quota_report(frozen, states)
    require(
        all(
            r["gap"] == 0
            and not any(r["difficulty_gaps"].values())
            and not any(r["category_gaps"].values())
            for r in report["rows"]
            if r["suite"] == "frozen"
        ),
        "冻结配额不完整",
    )
    return frozen, {
        "tasks": {t.task_id: fingerprint(t) for t in frozen},
        "development": {t.task_id: fingerprint(t) for t in dev},
        "files": files,
        "quota": report,
        "pressure_subsets": {
            tag: sorted(t.task_id for t in frozen if tag in t.pressure_tags)
            for tag in sorted({tag for t in frozen for tag in t.pressure_tags})
        },
    }


def tool_identity(task: EvaluationTask, root: Path) -> dict[str, Any]:
    from novacode.evaluation.specialty_tools import MCP_RECORDS, RecordTool, registry_for
    from novacode.permission import Mode
    from novacode.tool.exposure import BASE_TOOLS, ToolExposure

    env = json.loads((root / "public" / task.environment.path).read_text())
    records = env.get("tool_records", {})
    registry = registry_for(task.task_id, records, lambda *args, **kwargs: None)

    class McpRecord(RecordTool):
        def name(self) -> str:
            return "mcp__specialty__" + super().name()

    for name in MCP_RECORDS.get(task.task_id, []):
        # 仅固定定义；真实 MCP 连接等价由机制验收证明，不延迟正式连接。
        registry.register(McpRecord(name, records[name]))
    from novacode.evaluation.runner import allowed_names

    exposure = ToolExposure(registry, progressive=True)
    exposure.prepare(Mode.DEFAULT, frozenset(allowed_names(task)))
    deferred = [asdict(t) for t in exposure.definitions()]
    catalog = exposure.catalog()
    eager = ToolExposure(registry, progressive=False)
    eager.prepare(Mode.DEFAULT, frozenset(allowed_names(task)))
    definitions = [asdict(t) for t in eager.definitions()]
    require({t["name"] for t in deferred} == BASE_TOOLS | {"discover_tools"}, "基础集合变化")
    return {
        "allowed": sorted(allowed_names(task)),
        "definitions": definitions,
        "deferred_initial": deferred,
        "catalog": catalog,
        "connections": MCP_RECORDS.get(task.task_id, []),
        "catalog_and_schema_cost": "provider-request-ledger",
    }


def input_guard(
    task: EvaluationTask, root: Path, tools: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    from novacode.llm import Message, Request, System, ToolDefinition
    from novacode.prompt import build_system_prompt

    # 三组使用相同初始请求；采用源码/题面/Schema全部字节的保守上界，不假称实测Token。
    request = public_requests(task, root / "public")[0]
    initial = json.loads((root / "public" / task.initial_state.path).read_text())
    system = build_system_prompt(instructions=policy["instructions"], memory_index="")
    # 环境文本只含路径、版本、日期和平台；固定预留比实际环境块更大的4096字节。
    envelope = Request(
        messages=[Message(role="user", content=request)],
        tools=[ToolDefinition(**t) for t in tools["definitions"]],
        system=System(stable=system, environment=tools["catalog"] + " " * 4096),
    )
    bytes_count = len(json.dumps(asdict(envelope), ensure_ascii=False).encode())
    require(
        bytes_count + policy["max_output_tokens"] < policy["context_window"],
        "初始输入对任一配置不合法",
    )
    return {
        "initial_request_utf8_bytes": bytes_count,
        "window": policy["context_window"],
        "all_configurations_legal": True,
        "initial_assets_bytes": len(json.dumps(initial, ensure_ascii=False).encode()),
        "estimate": "conservative-bytes-not-measured-usage",
    }


def verify_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    require(digest(path) == expected_sha256, "冻结清单指纹变化")
    data: dict[str, Any] = json.loads(path.read_text())
    require(
        data["schema_version"] == 1
        and data["state"] == "FROZEN_VALID"
        and data["formal_model_calls"] == 0,
        "无可执行冻结身份",
    )
    repo = Path(data["repository_root"])
    require(source_files(repo) == data["source_files"], "源码树或锁文件变化，拒绝正式执行")
    require(version("novacode") == data["version"], "安装版本变化")
    require(set(data["sdk_versions"]) == {"anthropic", "mcp", "httpx", "openai"}, "SDK身份缺失")
    require(str(Path(data["runtime_path"]).resolve()) in data["external_files"], "运行时身份缺失")
    require(
        {name: version(name) for name in data["sdk_versions"]} == data["sdk_versions"],
        "SDK安装版本变化",
    )
    require(re.fullmatch(r"[0-9a-f]{40}", data["source_base_commit"]) is not None, "源码base未固定")
    hardware = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "docker": subprocess.check_output(
            ["docker", "version", "--format", "{{.Server.Version}}"], text=True
        ).strip(),
    }
    require(hardware == data["hardware"], "主机/容器引擎身份变化")
    require(
        data["limits"]
        == {
            "concurrency": 1,
            "live": {"cpus": 2, "memory_bytes": 4294967296, "pids": 256},
            "specialty": {"cpus": 1, "memory_bytes": 536870912, "pids": 64},
        },
        "资源身份变化",
    )
    require(digest(repo / data["protocol_path"]) == data["protocol_sha256"], "协议变化")
    tasks, corpus = corpus_identity(repo, data["asset_roots"], data["selection_ledgers"])
    require(corpus == data["corpus"], "冻结任务/验收/家族或证据变化")
    require(
        all(r["frozen"] == 24 for r in corpus["quota"]["rows"] if r["suite"] == "frozen"),
        "任务未全部冻结",
    )
    policy, budgets = data["policy"], data["budgets"]
    validate_policy(policy, budgets, tasks)
    require(
        digest(repo / data["budget_evidence_path"]) == data["budget_evidence_sha256"],
        "预算依据变化",
    )
    require(
        json.loads((repo / data["budget_evidence_path"]).read_text()) == budgets, "校准数值变化"
    )
    for name, sha in data["external_files"].items():
        require(digest(Path(name)) == sha, "运行时/官方数据/判题身份变化")
    require(
        plan_runs(tasks, budgets, data["campaign_id"], policy["seed"]) == data["plan"],
        "计划或配对顺序变化",
    )
    campaign = EvaluationCampaign.from_json(json.dumps(data["campaign"]))
    require(campaign == campaign_contract(data, tasks), "Campaign合同变化")
    tools = {t.task_id: tool_identity(t, repo / data["asset_roots"][t.source]) for t in tasks}
    require(tools == data["tools"], "工具能力/定义/目录变化")
    require(
        {
            t.task_id: input_guard(
                t, repo / data["asset_roots"][t.source], tools[t.task_id], policy
            )
            for t in tasks
        }
        == data["input_guards"],
        "初始输入保护变化",
    )
    return data


def validate_execution(
    payload: dict[str, Any], task: EvaluationTask, runtime: Path, public: Path, hidden: Path
) -> None:
    data = verify_manifest(Path(payload["frozen_manifest"]), payload["freeze_sha256"])
    require(
        task.suite == "frozen" and data["corpus"]["tasks"][task.task_id] == fingerprint(task),
        "合同不属于冻结集",
    )
    expected_root = Path(data["repository_root"]) / data["asset_roots"][task.source]
    require(
        public.resolve() == (expected_root / "public").resolve()
        and hidden.resolve() == (expected_root / "external").resolve(),
        "资产根变化",
    )
    row = next((r for r in data["plan"] if r["run_id"] == payload["run_id"]), None)
    require(row is not None, "执行不属于原计划")
    assert row is not None
    for key in ("campaign_id", "task_id", "config_id", "repeat_id", "limits"):
        require(payload[key] == row[key], "计划配置或预算变化")
    require(
        payload.get("context_compression", row["config_id"] != "no-compression")
        == (row["config_id"] != "no-compression")
        and payload.get("progressive_tool_schema", row["config_id"] != "eager-schema")
        == (row["config_id"] != "eager-schema"),
        "实际消融策略变化",
    )
    require(payload["total_limits"] == data["budgets"]["total"], "总预算变化")
    policy = data["policy"]
    cfg = payload["provider"]
    expected = {
        "protocol": policy["protocol"],
        "model": policy["requested_model"],
        "base_url": policy["endpoint"],
        "thinking": policy["thinking"],
        "max_retries": policy["sdk_max_retries"],
        "timeout": policy["timeout_seconds"],
        "max_output_tokens": policy["max_output_tokens"],
        "context_window": policy["context_window"],
    }
    require(all(cfg[k] == value for k, value in expected.items()), "模型实际参数变化")
    for key in ("instructions", "permissions", "usage_rule"):
        require(payload[key] == policy[key], "指令/授权/用量规则变化")
    require(payload["allowed_tools"] == data["tools"][task.task_id]["allowed"], "工具集合变化")
    require(
        runtime.resolve() == Path(data["runtime_path"]).resolve()
        and digest(runtime)
        == payload["runtime"]["sha256"]
        == data["external_files"][str(runtime.resolve())],
        "部署运行时变化",
    )
