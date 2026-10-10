"""单题 tmux 产品验收，复用已入库合同和有限预算，不启动开发先导。"""

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

from novacode.evaluation.contracts import EvaluationTask

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "specialty"))
import run as validation  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run", default="tmux-live")
    args = parser.parse_args()
    task = EvaluationTask.from_json((args.assets / "contracts" / (args.task + ".json")).read_text())
    # 单题产品验收按来源区分预留；总预算不变，失败预留不退还。
    validation.LIMITS = replace(
        validation.LIMITS, tokens=400_000 if task.source == "live" else 200_000
    )
    asyncio.run(
        validation.run(
            args.assets, args.campaign, args.archive.resolve(), args.run, args.task, True
        )
    )
