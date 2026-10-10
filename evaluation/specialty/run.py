"""有界代表验收；复用运行器与既有 tmux 驱动，不启动开发先导 campaign。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from novacode.config import load
from novacode.evaluation.assets import public_requests
from novacode.evaluation.budget import BudgetLimits
from novacode.evaluation.contracts import EvaluationTask
from novacode.evaluation.runner import allowed_names, run_task

REPO = Path(__file__).resolve().parents[2]
LIMITS = BudgetLimits(
    seconds=300,
    tokens=200_000,
    provider_calls=20,
    tool_calls=30,
    single_request_tokens=30_000,
    cleanup_seconds=20,
)
TOTAL = replace(LIMITS, seconds=1200, tokens=800_000, provider_calls=80, tool_calls=120)


async def run(
    assets: Path, campaign: Path, archive: Path, name: str, task_id: str, tui: bool
) -> None:
    task = EvaluationTask.from_json((assets / "contracts" / (task_id + ".json")).read_text())
    cfg = next(
        p
        for p in load(str(REPO / ".novacode/config.yaml")).providers
        if p.model == "deepseek-v4-flash" and p.protocol == "anthropic"
    )
    cfg = replace(cfg, max_retries=0, timeout=90, max_output_tokens=4096, thinking=True)
    with archive.open("rb") as stream:
        runtime_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    usage = (
        REPO
        / "openspec/changes/add-reproducible-agent-evaluation/evidence"
        / "stage-4/usage-probe/usage.json"
    )
    payload = {
        "run_id": name,
        "campaign_id": campaign.name,
        "config_id": "full",
        "repeat_id": 1,
        "provider": asdict(cfg),
        "allowed_tools": sorted(allowed_names(task)),
        "permissions": {"allow": ["Write", "Edit", "Bash"], "deny": []},
        "instructions": "只操作当前任务目录，不修改运行时安装，不提交 Git。使用任务项目 Python。",
        "limits": asdict(LIMITS),
        "total_limits": asdict(TOTAL),
        "runtime": {"sha256": runtime_sha},
        "usage_rule": {
            "rule_id": "deepseek-anthropic-cache-separate-stage4-v1",
            "protocol": "anthropic",
            "cache_mode": "separate",
            "evidence_sha256": hashlib.sha256(usage.read_bytes()).hexdigest(),
        },
    }
    output = campaign / "runs" / name
    future = asyncio.create_task(
        run_task(
            task,
            public_root=assets / "public",
            acceptance_root=assets / "external",
            runtime_archive=archive,
            payload=payload,
            output=output,
            tui=tui,
        )
    )
    if tui:
        # 复用正式 TUI 的真实键盘输入/屏幕/退出链路；不自动发送隐藏验收反馈。
        sys.path.insert(0, str(REPO / "evaluation/examples"))
        await importlib.import_module("stage4_live").tmux_run(
            output, future, public_requests(task, assets / "public")[0]
        )
    result = await future
    identity = {
        "version": __import__("novacode").__version__,
        "runtime_sha256": runtime_sha,
        "limits": asdict(LIMITS),
        "total": asdict(TOTAL),
        "files": {
            str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((REPO / "src").rglob("*.py"))
        },
    }
    (output / "source-identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    print(json.dumps({**asdict(result), "strict_success": result.strict_success}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--task", choices=["S05", "S12"], required=True)
    parser.add_argument("--tui", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.assets, args.campaign, args.archive, args.run, args.task, args.tui))
