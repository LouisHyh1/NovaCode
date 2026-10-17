"""只读检查正式队列完成度、总预算预留及存储余量。"""

import json
import shutil
import sys
from collections import Counter
from pathlib import Path


def status(root: Path, manifest_path: Path | None = None) -> dict[str, object]:
    from evaluation.formal.run import MANIFEST
    from novacode.evaluation.contracts import require
    from novacode.evaluation.frozen import digest

    manifest_path = manifest_path or MANIFEST
    manifest = json.loads(manifest_path.read_text())
    identity_path = root / "identity.json"
    if identity_path.exists():
        identity = json.loads(identity_path.read_text())
        require(
            identity["manifest_sha256"] == digest(manifest_path)
            and identity["campaign_id"] == manifest["campaign_id"],
            "状态读取混入不同 campaign",
        )
    rows = [json.loads(p.read_text()) for p in (root / "runs").glob("*/result.json")]
    recorded = [r for r in rows if r["state"] == "RECORDED"]
    require(all(r["campaign_id"] == manifest["campaign_id"] for r in rows), "状态结果跨 campaign")
    reserved = [
        json.loads(line)
        for p in (root / "runs").glob("campaign-*.jsonl")
        for line in p.read_text().splitlines()
    ]
    last = [
        json.loads(p.read_text())["sequence"]
        for p in (root / "runs").glob("*/execution-identity.json")
    ]
    return {
        "campaign_id": manifest["campaign_id"],
        "planned": len(manifest["plan"]),
        "independent_tasks": len(manifest["corpus"]["tasks"]),
        "recorded": len(recorded),
        "reserved_or_running": len(reserved),
        "last_recorded_sequence": max(last, default=0),
        "termination": dict(Counter(r["termination"] for r in recorded)),
        "reserved_tokens": sum(r["limits"]["tokens"] for r in reserved),
        "remaining_reserved_token_capacity": reserved[0]["total"]["tokens"]
        - sum(r["limits"]["tokens"] for r in reserved)
        if reserved
        else None,
        "free_gib": round(shutil.disk_usage(root).free / 1024**3, 2),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            status(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None),
            ensure_ascii=False,
        )
    )
