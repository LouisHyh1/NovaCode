"""只读导出代表运行并复核账本、源码身份、完整成本及凭据未落盘。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

from novacode.config import load
from novacode.evaluation.contracts import require
from novacode.evaluation.ledger import read_records, rebuild

REPO = Path(__file__).resolve().parents[2]


def review(campaign: Path, wheel: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    keys = [
        p.api_key.encode() for p in load(str(REPO / ".novacode/config.yaml")).providers if p.api_key
    ]
    summary = []
    for name in ("auto-s12", "tmux-s05"):
        run = campaign / "runs" / name
        result = json.loads((run / "result.json").read_text())
        require(
            result["termination"] == "completed"
            and result["acceptance"] == "passed"
            and result["scope"] == result["cleanup"] == "passed",
            "代表任务不是严格成功",
        )
        identity = json.loads((run / "source-identity.json").read_text())
        with zipfile.ZipFile(wheel) as archive:
            require(
                not any(
                    "definitions" in name or "acceptance" in name for name in archive.namelist()
                ),
                "wheel 混入隐藏资产",
            )
            for name_in_repo, digest in identity["files"].items():
                require(
                    hashlib.sha256((REPO / name_in_repo).read_bytes()).hexdigest() == digest,
                    "代表验收后源码改变",
                )
                require(
                    hashlib.sha256(archive.read(name_in_repo.removeprefix("src/"))).hexdigest()
                    == digest,
                    "实际部署 wheel 与交付源码不一致",
                )
        ledger = run / "worker/ledger.jsonl"
        metrics = rebuild(ledger)
        require(
            metrics["unknown_usage_requests"] == metrics["incomplete_tools"] == 0
            and not metrics["truncated_tail"]
            and metrics["incomplete_phases"] == 0,
            "代表账本不完整",
        )
        records, _ = read_records(ledger)
        if name == "tmux-s05":
            require(
                metrics["fault_injections"] == 1 and metrics["tool_errors"] >= 1, "未触发规定故障"
            )
            require((run / "tui-exited").read_text().strip() == "0", "tmux 未正常退出")
        else:
            require(metrics["discovery_calls"] >= 2, "跨来源发现未覆盖")
            calls = {r["data"]["name"] for r in records if r["kind"] == "tool_start"}
            require({"issue_lookup", "mcp__specialty__ci_commit"} <= calls, "缺少混合业务工具调用")
        for path in run.rglob("*"):
            if path.is_file() and not path.is_symlink():
                raw = path.read_bytes()
                require(not any(key in raw for key in keys), "运行证据含凭据")
        dest = output / run.name
        dest.mkdir()
        for filename in (
            "result.json",
            "source-identity.json",
            "orchestration.jsonl",
            "judge.json",
        ):
            shutil.copy2(run / filename, dest / filename)
        shutil.copy2(ledger, dest / "ledger.jsonl")
        for filename in ("submission.diff", "tmux-pane.txt"):
            path = run / filename
            if path.exists():
                (dest / (filename + ".gz")).write_bytes(gzip.compress(path.read_bytes(), mtime=0))
        with tarfile.open(dest / "worker.tar.gz", "w:gz") as archive:
            archive.add(run / "worker", arcname="worker")
        summary.append(
            {
                "run_id": run.name,
                "task_id": result["task_id"],
                "strict_success": True,
                "metrics": metrics,
                "source_matches_wheel": True,
                "version": identity["version"],
            }
        )
    (output / "review.json").write_text(
        json.dumps({"schema_version": 1, "runs": summary, "secrets_absent": True}, indent=2) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review(args.campaign, args.wheel, args.output)
