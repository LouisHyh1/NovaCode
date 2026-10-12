"""有界开发先导；先运行代表配对块，通过复核后才由调用者显式扩展。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from novacode.config import load
from novacode.evaluation.assets import public_requests, validate_task_assets
from novacode.evaluation.campaign import reserve_run
from novacode.evaluation.contracts import EvaluationTask, fingerprint, require
from novacode.evaluation.pilot import limits_for, plan_runs, run_metrics
from novacode.evaluation.runner import allowed_names, run_task

REPO = Path(__file__).resolve().parents[2]
CACHE = Path.home() / ".cache/novacode-evaluation"


def save(path: Path, data: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inputs() -> tuple[EvaluationTask, ...]:
    tasks = tuple(
        EvaluationTask.from_json(p.read_text())
        for source in ("live", "specialty")
        for p in sorted((REPO / "evaluation" / source / "assets/contracts").glob("*.json"))
    )
    categories = frozenset(t.budget_category for t in tasks)
    for task in tasks:
        assets = REPO / "evaluation" / task.source / "assets"
        validate_task_assets(task, assets / "public", assets / "external", categories)
    require(
        json.loads((REPO / "evaluation/live/assets/admitted.json").read_text())["state"]
        == "DEV_ADMITTED",
        "开发集尚未入库",
    )
    return tasks


def setup(root: Path) -> None:
    tasks = inputs()
    policy = json.loads((Path(__file__).with_name("policy.json")).read_text())
    plan = plan_runs(policy, tasks)
    root.mkdir(parents=True, exist_ok=False)
    (root / "runs").mkdir()
    save(root / "policy.json", policy)
    save(root / "plan.json", plan)
    wheels = root / "wheels"
    wheels.mkdir()
    for wheel in (CACHE / "stage1/wheels").glob("*.whl"):
        if not wheel.name.startswith("novacode-"):
            shutil.copy2(wheel, wheels)
    subprocess.run(
        [str(Path.home() / ".local/bin/uv"), "build", "--wheel", "--out-dir", str(wheels)],
        cwd=REPO,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "evaluation/preflight/pack_agent_runtime.py",
            "--python-home",
            str(Path.home() / ".local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu"),
            "--uv",
            str(Path.home() / ".local/bin/uv"),
            "--wheelhouse",
            str(wheels),
            "--output",
            str(root / "runtime.tar"),
        ],
        cwd=REPO,
        check=True,
    )
    save(
        root / "identity.json",
        {
            "schema_version": 1,
            "version": __import__("novacode").__version__,
            "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO)
            .decode()
            .strip(),
            "files": {
                str(p.relative_to(REPO)): digest(p)
                for p in [
                    *sorted((REPO / "src").rglob("*.py")),
                    REPO / "uv.lock",
                    REPO / "evaluation/pilot/policy.json",
                ]
            },
            "tasks": {t.task_id: fingerprint(t) for t in tasks},
            "runtime_sha256": digest(root / "runtime.tar"),
            "served_model": policy["served_model"],
            "requested_model": policy["requested_model"],
            "model_version_pin": "官方当前映射；接口不支持不可变权重版本，不能保证远端不升级",
        },
    )


def verify(root: Path) -> tuple[dict[str, Any], tuple[EvaluationTask, ...]]:
    tasks = inputs()
    policy = json.loads((root / "policy.json").read_text())
    require(
        plan_runs(policy, tasks) == json.loads((root / "plan.json").read_text()), "先导计划变化"
    )
    identity = json.loads((root / "identity.json").read_text())
    require(identity["tasks"] == {t.task_id: fingerprint(t) for t in tasks}, "任务身份变化")
    for name, sha in identity["files"].items():
        require(digest(REPO / name) == sha, "运行代码/策略变化，必须创建新campaign")
    require(digest(root / "runtime.tar") == identity["runtime_sha256"], "runtime变化")
    return policy, tasks


async def probe(root: Path) -> None:
    from novacode.evaluation.budget import BudgetLimits

    policy, _ = verify(root)
    reserve_run(
        root / "runs",
        root.name,
        "usage-probe",
        BudgetLimits(**policy["probe"]),
        BudgetLimits(**policy["total"]),
    )
    # 复用既有公开文本探针：仅核验缓存字段，thinking=false与主集执行分列。
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(REPO / "evaluation/examples/stage4_usage_probe.py"),
        str(root / "usage-probe"),
        cwd=REPO,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    (root / "probe-output.json").write_bytes(stdout)
    require(proc.returncode == 0, f"用量核验失败，exit={proc.returncode}，不扩展先导")
    require(not stderr, "用量探针有诊断输出，须人工核验")
    rows = json.loads((root / "usage-probe/usage.json").read_text())
    native = rows[-1]["raw_usage"]
    require(rows[1]["raw_usage"]["cache_read_input_tokens"] > 0, "缓存未命中，规则未核验")
    for row in rows[:2]:
        u = row["raw_usage"]
        require(
            u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]
            == native["prompt_tokens"],
            "实际缓存字段语义发生变化",
        )


async def run_one(root: Path, row: dict[str, Any], *, tui: bool = False) -> None:
    policy, tasks = verify(root)
    task = next(t for t in tasks if t.task_id == row["task_id"])
    cfg = next(
        p
        for p in load(str(REPO / ".novacode/config.yaml")).providers
        if p.model == policy["requested_model"] and p.protocol == "anthropic"
    )
    require(cfg.base_url == policy["endpoint"], "实际端点与先导身份不一致")
    cfg = replace(
        cfg,
        max_retries=0,
        timeout=policy["timeout_seconds"],
        max_output_tokens=policy["max_output_tokens"],
        thinking=True,
        context_window=policy["context_window"],
    )
    output = root / "runs" / row["run_id"]
    data = {
        **row,
        "campaign_id": root.name,
        "provider": asdict(cfg),
        "total_limits": policy["total"],
        "allowed_tools": sorted(allowed_names(task)),
        "permissions": {"allow": ["Write", "Edit", "Bash"], "deny": []},
        "instructions": policy["instructions"],
        "runtime": {"sha256": digest(root / "runtime.tar")},
        "usage_rule": {
            "rule_id": "deepseek-anthropic-cache-separate-stage9-v1",
            "protocol": "anthropic",
            "cache_mode": "separate",
            "evidence_sha256": digest(root / "usage-probe/usage.json"),
        },
    }
    assets = REPO / "evaluation" / task.source / "assets"
    future = asyncio.create_task(
        run_task(
            task,
            public_root=assets / "public",
            acceptance_root=assets / "external",
            runtime_archive=(root / "runtime.tar").resolve(),
            payload=data,
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
    save(output / "source-identity.json", json.loads((root / "identity.json").read_text()))
    metrics = run_metrics(output)
    print(
        json.dumps(
            {
                "run": row["run_id"],
                "termination": result.termination,
                "strict_success": result.strict_success,
                "metrics": metrics.get("metrics"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    require(bool(result.termination), f"环境阻塞: {result.block_reason}")


async def execute(root: Path, mode: str) -> None:
    policy, tasks = verify(root)
    if mode == "probe":
        await probe(root)
        return
    plan = json.loads((root / "plan.json").read_text())
    if mode == "representative":
        plan = [r for r in plan if r["task_id"] in ("S01", "L02")]
    elif mode == "remaining":
        for row in plan[:6]:
            data = run_metrics(root / "runs" / row["run_id"])
            require(data["started"] and data["metrics"] is not None, "代表题缺少真实账本")
            require(
                data["metrics"]["provider_calls"] > 0
                and data["metrics"]["unknown_usage_requests"] == 0
                and data["result"]["cleanup"] == "passed",
                "代表轨迹不足，不能扩展",
            )
        plan = plan[6:]
    elif mode == "tmux":
        task = next(t for t in tasks if t.task_id == "S01")
        plan = [
            {
                "run_id": "tmux-S01-" + c,
                "task_id": "S01",
                "config_id": c,
                "repeat_id": 1,
                "limits": asdict(limits_for(policy, task)),
            }
            for c in ("full", "no-compression", "eager-schema")
        ]
    for row in plan:
        output = root / "runs" / row["run_id"]
        if output.exists():
            require((output / "result.json").exists(), "已开始记录不重置；需另行审计中断")
            require(
                json.loads((output / "result.json").read_text())["state"] == "RECORDED",
                "前置阻塞必须使用新恢复身份",
            )
            continue
        await run_one(root, row, tui=mode == "tmux")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["setup", "probe", "representative", "remaining", "tmux"])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "setup":
        setup(args.root.resolve())
    else:
        asyncio.run(execute(args.root.resolve(), args.mode))
