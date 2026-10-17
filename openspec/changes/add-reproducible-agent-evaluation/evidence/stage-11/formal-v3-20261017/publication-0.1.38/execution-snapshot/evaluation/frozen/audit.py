"""无模型的完整冻结与实际参数变更拒绝审计，不改写原计划或原结果。"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from novacode.evaluation.frozen import (  # noqa: E402
    digest,
    load_tasks,
    validate_execution,
    verify_manifest,
)


def rejected_check(check: Callable[[], object], name: str, rejected: list[str]) -> None:
    try:
        check()
    except (KeyError, ValueError, TypeError):
        rejected.append(name)
    else:
        raise ValueError("必要身份变化未被拒绝: " + name)


def audit(manifest: Path, output: Path) -> None:
    data = verify_manifest(manifest, digest(manifest))
    row = data["plan"][0]
    root = REPO / data["asset_roots"]["specialty"]
    task = load_tasks(root)[0]
    row = next(r for r in data["plan"] if r["task_id"] == task.task_id)
    policy = data["policy"]
    runtime = Path(data["runtime_path"])
    payload = {
        **row,
        "frozen_manifest": str(manifest),
        "freeze_sha256": digest(manifest),
        "provider": {
            "name": "offline-audit",
            "api_key": "unused-no-network",
            "protocol": policy["protocol"],
            "model": policy["requested_model"],
            "base_url": policy["endpoint"],
            "thinking": policy["thinking"],
            "max_retries": policy["sdk_max_retries"],
            "timeout": policy["timeout_seconds"],
            "max_output_tokens": policy["max_output_tokens"],
            "context_window": policy["context_window"],
        },
        "total_limits": data["budgets"]["total"],
        "instructions": policy["instructions"],
        "permissions": policy["permissions"],
        "usage_rule": policy["usage_rule"],
        "allowed_tools": data["tools"][task.task_id]["allowed"],
        "runtime": {"sha256": digest(runtime)},
    }
    validate_execution(payload, task, runtime, root / "public", root / "external")
    rejected: list[str] = []
    for key in (
        "frozen_manifest",
        "freeze_sha256",
        "campaign_id",
        "task_id",
        "config_id",
        "repeat_id",
        "limits",
        "total_limits",
        "provider",
        "instructions",
        "permissions",
        "usage_rule",
        "allowed_tools",
        "runtime",
    ):
        candidate = copy.deepcopy(payload)
        del candidate[key]
        rejected_check(
            lambda: validate_execution(
                candidate, task, runtime, root / "public", root / "external"
            ),
            "missing-payload-" + key,
            rejected,
        )
    for key in (
        "model",
        "base_url",
        "max_retries",
        "max_output_tokens",
        "context_window",
        "timeout",
        "thinking",
    ):
        candidate = copy.deepcopy(payload)
        candidate["provider"][key] = "changed"
        rejected_check(
            lambda: validate_execution(
                candidate, task, runtime, root / "public", root / "external"
            ),
            "changed-provider-" + key,
            rejected,
        )
    for key in ("context_compression", "progressive_tool_schema"):
        candidate = copy.deepcopy(payload)
        candidate[key] = not (
            row["config_id"]
            != ("no-compression" if key == "context_compression" else "eager-schema")
        )
        rejected_check(
            lambda: validate_execution(
                candidate, task, runtime, root / "public", root / "external"
            ),
            "changed-strategy-" + key,
            rejected,
        )
    with tempfile.TemporaryDirectory(prefix="freeze-manifest-negative-") as directory:
        path = Path(directory) / "manifest.json"
        for key in (
            "source_files",
            "policy",
            "budgets",
            "corpus",
            "plan",
            "tools",
            "external_files",
            "sdk_versions",
        ):
            candidate = copy.deepcopy(data)
            del candidate[key]
            path.write_text(json.dumps(candidate, ensure_ascii=False))
            rejected_check(
                lambda: verify_manifest(path, digest(path)), "missing-manifest-" + key, rejected
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(
            {
                "schema_version": 1,
                "state": "FROZEN_VALID",
                "valid_payload_passed": True,
                "rejected": rejected,
                "model_calls": 0,
                "source_changes": False,
            },
            stream,
            indent=2,
        )
        stream.write("\n")
    print("frozen drift audit:", len(rejected), "rejections, zero model calls")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.manifest.resolve(), args.output.resolve())
