"""跨进程先导准入账本；预留整次上限，不因失败或未知用量退还。"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from novacode.evaluation.budget import BudgetLimits
from novacode.evaluation.contracts import require


def reserve_run(
    root: Path,
    campaign_id: str,
    run_id: str,
    limits: BudgetLimits,
    total: BudgetLimits,
) -> None:
    import fcntl

    name = hashlib.sha256(campaign_id.encode()).hexdigest()
    path = root / ("campaign-" + name + ".jsonl")
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        rows = [json.loads(line) for line in stream]
        require(all(row["total"] == asdict(total) for row in rows), "Campaign 总预算不能改变")
        require(all(row["run_id"] != run_id for row in rows), "运行已预留，不得覆盖或重启")
        for field in ("tokens", "provider_calls", "tool_calls", "seconds"):
            used = sum(row["limits"][field] for row in rows)
            require(used + getattr(limits, field) <= getattr(total, field), "Campaign 总预算不足")
        stream.write(
            json.dumps({"run_id": run_id, "limits": asdict(limits), "total": asdict(total)}) + "\n"
        )
        stream.flush()
        import os

        os.fsync(stream.fileno())
