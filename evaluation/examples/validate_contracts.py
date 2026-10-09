"""校验样例资产并生成合同计数示例；不会调用模型或登记真实入库任务。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from novacode.evaluation.assets import validate_task_assets
from novacode.evaluation.contracts import EvaluationTask, fingerprint
from novacode.evaluation.corpus import quota_report, validate_corpus


def main() -> None:
    root = Path(__file__).resolve().parent
    tasks = tuple(
        EvaluationTask.from_json(path.read_text(encoding="utf-8"))
        for path in sorted((root / "contracts").glob("*.json"))
    )
    validate_corpus(tasks)
    scripts = [
        validate_task_assets(task, root / "public", root / "external", frozenset({"example-small"}))
        for task in tasks
    ]
    print(
        json.dumps(
            {
                "example_only": True,
                "schema_version": 1,
                "evaluation_tasks": len(tasks),
                "planned_evaluation_runs_for_one_config_and_repeat": len(tasks),
                "planned_agent_runs": sum(len(script) for script in scripts),
                "model_calls_started": 0,
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "contract_sha256": fingerprint(task),
                        "requests": len(script),
                        "contract": asdict(task),
                    }
                    for task, script in zip(tasks, scripts, strict=True)
                ],
                "quotas": quota_report(tasks, {task.task_id: "CANDIDATE" for task in tasks}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
