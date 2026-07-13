"""Session-local compaction state."""

import copy
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from novacode.compact.const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


@dataclass
class FileReadRecord:
    path: str
    content: str
    timestamp: datetime


@dataclass
class SessionContext:
    session_id: str
    spill_dir: str


class ContentReplacementState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._seen_ids: set[str] = set()
        self._replacements: dict[str, str] = {}

    def decide_once(
        self,
        tool_use_id: str,
        original_content: str,
        decide: Callable[[], tuple[str, str]],
    ) -> str:
        with self._lock:
            if tool_use_id in self._seen_ids:
                return self._replacements.get(tool_use_id, original_content)

            decision, preview = decide()
            if decision == "replaced":
                self._seen_ids.add(tool_use_id)
                self._replacements[tool_use_id] = preview
                return preview
            if decision == "kept":
                self._seen_ids.add(tool_use_id)
                return original_content
            if decision == "skip":
                return original_content
            raise ValueError(f"unknown replacement decision: {decision}")


class CompactCircuitBreaker:
    def __init__(self) -> None:
        self._consecutive_failures = 0

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1

    def tripped(self) -> bool:
        return self._consecutive_failures >= MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


class RecoveryState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: dict[str, FileReadRecord] = {}

    def record_file(self, path: str, content: str) -> None:
        abs_path = str(Path(path).resolve())
        with self._lock:
            self._files[abs_path] = FileReadRecord(
                path=abs_path,
                content=content,
                timestamp=datetime.now(UTC),
            )

    def snapshot(self) -> list[FileReadRecord]:
        with self._lock:
            records = copy.deepcopy(list(self._files.values()))
        return sorted(records, key=lambda r: r.timestamp, reverse=True)


def new_session_context(workspace: str) -> SessionContext:
    session_id = uuid.uuid4().hex
    spill_dir = Path(workspace) / ".novacode" / "sessions" / session_id / "tool-results"
    spill_dir.mkdir(parents=True, exist_ok=True)
    return SessionContext(session_id=session_id, spill_dir=str(spill_dir))
