"""仅新增的 JSONL 原账本、产物指纹与中断重建。"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from novacode.evaluation.contracts import SCHEMA_VERSION, require, text
from novacode.privacy import redact


def sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: Path, run_id: str, *, secrets: tuple[str, ...] = ()) -> None:
        text(run_id, "run_id")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.run_id = run_id
        self.secrets = secrets
        self.sequence = 0
        self._stream = path.open("x", encoding="utf-8")

    def append(self, kind: str, **data: Any) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "sequence": self.sequence + 1,
            "kind": kind,
            "utc": datetime.now(UTC).isoformat(),
            "monotonic": time.monotonic(),
            "data": redact(data, self.secrets),
        }
        record["sha256"] = sha256(record)
        self._stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self.sequence += 1

    def artifact(self, content: str) -> dict[str, str]:
        safe = str(redact(content, self.secrets))
        digest = hashlib.sha256(safe.encode("utf-8")).hexdigest()
        root = self.path.parent / "artifacts"
        root.mkdir(exist_ok=True)
        path = root / (digest + ".txt")
        if not path.exists():
            with path.open("x", encoding="utf-8") as stream:
                stream.write(safe)
                stream.flush()
                os.fsync(stream.fileno())
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "产物指纹不符")
        return {"path": path.relative_to(self.path.parent).as_posix(), "sha256": digest}

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        require(
            name in ("preparation", "initialization", "agent", "cleanup", "grading"),
            "未知时间边界",
        )
        phase_id = uuid4().hex
        start = time.monotonic()
        self.append("phase_start", phase=name, phase_id=phase_id)
        status = "completed"
        try:
            yield
        except BaseException:
            status = "interrupted"
            raise
        finally:
            self.append(
                "phase_end",
                phase=name,
                phase_id=phase_id,
                seconds=time.monotonic() - start,
                status=status,
            )

    def close(self) -> None:
        self._stream.close()


def read_records(path: Path) -> tuple[list[dict[str, Any]], bool]:
    records: list[dict[str, Any]] = []
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    truncated = bool(lines and not lines[-1].endswith(b"\n"))
    for line in lines[:-1] if truncated else lines:
        record = json.loads(line)
        digest = record.pop("sha256")
        require(sha256(record) == digest, "账本内容指纹不符")
        require(record["sequence"] == len(records) + 1, "账本序号不连续")
        require(record["schema_version"] == SCHEMA_VERSION, "账本版本不符")
        if records:
            require(record["run_id"] == records[0]["run_id"], "账本运行身份混用")
        record["sha256"] = digest
        records.append(record)
    return records, truncated


def rebuild(path: Path) -> dict[str, Any]:
    records, truncated = read_records(path)
    requests: dict[str, dict[str, Any]] = {}
    tools: dict[str, dict[str, Any]] = {}
    phases: dict[str, dict[str, Any]] = {}
    for record in records:
        kind, data = record["kind"], record["data"]
        if kind == "request_usage":
            target = requests.get(data["request_id"], {})
            require("start" in target, "用量缺少开始事件")
            target["usage"] = data["usage"]
            continue
        if kind.startswith("request_") and kind in ("request_start", "request_end"):
            key = data["request_id"]
            target = requests.setdefault(key, {})
        elif kind in ("tool_start", "tool_end"):
            key = data["invocation_id"]
            target = tools.setdefault(key, {})
        elif kind in ("phase_start", "phase_end"):
            key = data["phase_id"]
            target = phases.setdefault(key, {})
        else:
            continue
        side = "start" if kind.endswith("_start") else "end"
        require(side not in target, "同一调用重复计账")
        require(side == "start" or "start" in target, "缺少开始事件")
        target[side] = {**data, "monotonic": record["monotonic"]}
    usages = [item.get("end", {}).get("usage", item.get("usage", {})) for item in requests.values()]
    unknown = sum(usage.get("measured_total") is None for usage in usages)
    lower = sum(usage.get("known_lower_bound", 0) for usage in usages)
    durations = {}
    for name in ("preparation", "initialization", "agent", "cleanup", "grading"):
        samples = [p for p in phases.values() if p["start"]["phase"] == name]
        durations[name] = (
            sum(p["end"]["seconds"] for p in samples)
            if samples and all("end" in p for p in samples)
            else None
        )
    initial = [
        p["start"]["monotonic"] for p in phases.values() if p["start"]["phase"] == "initialization"
    ]
    cleanup = [
        p["end"]["monotonic"]
        for p in phases.values()
        if "end" in p and p["start"]["phase"] == "cleanup"
    ]
    ends = [item.get("end", {}) for item in tools.values()]
    return {
        "provider_calls": len(requests),
        "unknown_usage_requests": unknown,
        "known_token_lower_bound": lower,
        "measured_tokens": None if unknown else lower,
        "tool_calls": len(tools),
        "tool_retries": sum(item["start"].get("retry_of") is not None for item in tools.values()),
        "tool_executed": sum(
            item.get("execution") in ("succeeded", "failed", "cancelled") for item in ends
        ),
        "tool_succeeded": sum(item.get("execution") == "succeeded" for item in ends),
        "tool_denied": sum(item.get("authorization_status") == "denied" for item in ends),
        "tool_errors": sum(item.get("is_error", False) for item in ends),
        "discovery_calls": sum(
            item["start"]["name"] == "discover_tools" for item in tools.values()
        ),
        "incomplete_tools": sum("end" not in item for item in tools.values()),
        "phase_seconds": durations,
        "main_end_to_end_seconds": max(cleanup) - min(initial) if initial and cleanup else None,
        "total_wall_seconds": records[-1]["monotonic"] - records[0]["monotonic"]
        if records
        else None,
        "truncated_tail": truncated,
        "incomplete_phases": sum("end" not in item for item in phases.values()),
    }
