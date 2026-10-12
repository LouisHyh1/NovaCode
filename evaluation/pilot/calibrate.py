"""修订后的两道代表题复验；新Campaign与首轮共享不退还的总预算。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from novacode.evaluation.budget import BudgetLimits
from novacode.evaluation.campaign import reserve_run
from novacode.evaluation.contracts import CONFIGS, require
from novacode.evaluation.pilot import run_metrics

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evaluation.pilot.run import run_one, save, setup, verify  # noqa: E402


def plan(envelope: Path) -> list[dict[str, Any]]:
    policy = json.loads((envelope / "policy.json").read_text())
    normal = replace(
        BudgetLimits(**policy["temporary"]),
        seconds=200,
        tokens=100000,
        single_request_tokens=50000,
        provider_calls=12,
        tool_calls=15,
    )
    long = BudgetLimits(**policy["long_context"])
    return [
        {
            "run_id": f"calibration-{task}-{config}",
            "task_id": task,
            "config_id": config,
            "repeat_id": 1,
            "pair_id": task,
            "limits": asdict(limits),
        }
        for task, limits in (("S01", normal), ("S08", long))
        for config in CONFIGS
    ]


def available(envelope: Path, rows: list[dict[str, Any]]) -> None:
    files = list((envelope / "runs").glob("campaign-*.jsonl"))
    require(len(files) == 1, "先导总预留账本不唯一")
    reservations = [json.loads(line) for line in files[0].read_text().splitlines()]
    total = json.loads((envelope / "policy.json").read_text())["total"]
    for field in ("seconds", "tokens", "provider_calls", "tool_calls"):
        committed = sum(r["limits"][field] for r in reservations)
        pending = sum(
            r["limits"][field]
            for r in rows
            if r["run_id"] not in {x["run_id"] for x in reservations}
        )
        require(committed + pending <= total[field], f"跨轮总预算不足: {field}")


async def execute(root: Path, envelope: Path) -> None:
    policy, _ = verify(root)
    rows = json.loads((root / "calibration-plan.json").read_text())
    available(envelope, rows)
    # 仅核验已修订源码，不从复验成败重选代表题或覆盖第一轮结果。
    for row in rows:
        output = root / "runs" / row["run_id"]
        require(not output.exists(), "校准复验记录不可覆盖；中断需新身份审计")
        reserve_run(
            envelope / "runs",
            envelope.name,
            row["run_id"],
            BudgetLimits(**row["limits"]),
            BudgetLimits(**policy["total"]),
        )
        await run_one(root, row)
    save(
        root / "calibration-review.json",
        {
            "schema_version": 1,
            "source_round": "calibration-v2",
            "independent_new_tasks": 0,
            "main_matrix_replacements": 0,
            "runs": [run_metrics(root / "runs" / row["run_id"]) for row in rows],
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["setup", "run"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--envelope", type=Path, required=True)
    args = parser.parse_args()
    root, envelope = args.root.resolve(), args.envelope.resolve()
    if args.mode == "setup":
        rows = plan(envelope)
        available(envelope, rows)
        setup(root)
        (root / "usage-probe").mkdir()
        (root / "usage-probe/usage.json").write_bytes(
            (envelope / "usage-probe/usage.json").read_bytes()
        )
        save(root / "calibration-plan.json", rows)
    else:
        asyncio.run(execute(root, envelope))
