"""独立评测入口；计划与资产在主机，Agent 只见隔离容器的公开请求。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from novacode.evaluation.contracts import EvaluationTask
from novacode.evaluation.runner import run_task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--acceptance-root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tui", action="store_true")
    args = parser.parse_args()
    task = EvaluationTask.from_json(args.task.read_text())
    payload = json.loads(sys.stdin.readline())
    run = asyncio.run(
        run_task(
            task,
            public_root=args.public_root,
            acceptance_root=args.acceptance_root,
            runtime_archive=args.runtime,
            payload=payload,
            output=args.output,
            tui=args.tui,
        )
    )
    print(
        json.dumps(
            {
                "state": run.state,
                "termination": run.termination,
                "strict_success": run.strict_success,
            }
        )
    )


if __name__ == "__main__":
    main()
