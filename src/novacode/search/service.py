"""标准库实现的有界、非阻塞文件搜索服务。"""

from __future__ import annotations

import asyncio
import codecs
import fnmatch
import os
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from novacode.permission.sensitive import is_sensitive_selector
from novacode.runtime.errors import OperationCancelled, OperationTimeout, ValidationError
from novacode.search.domain import (
    ReadBudget,
    ReadRequest,
    ReadResult,
    SearchBudget,
    SearchKind,
    SearchRequest,
    SearchResult,
)

_CHUNK_SIZE = 64 * 1024
_IGNORED_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "venv",
}


class _ScanStopped(RuntimeError):  # noqa: N818 - 扫描内部控制流，不是公共错误
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(slots=True)
class _ScanState:
    hits: list[str] = field(default_factory=list)
    scanned_files: int = 0
    scanned_bytes: int = 0


class FileSearchService:
    def __init__(
        self,
        *,
        default_budget: SearchBudget | None = None,
        default_read_budget: ReadBudget | None = None,
        cancellation_grace: float = 1.0,
    ) -> None:
        self.default_budget = default_budget or SearchBudget()
        self.default_read_budget = default_read_budget or ReadBudget()
        self.cancellation_grace = cancellation_grace
        self._active_workers = 0

    @property
    def active_workers(self) -> int:
        return self._active_workers

    async def search(self, request: SearchRequest) -> SearchResult:
        stop = threading.Event()
        self._active_workers += 1

        async def run_worker() -> SearchResult:
            try:
                return await asyncio.to_thread(self._search_sync, request, stop)
            finally:
                self._active_workers -= 1

        worker = asyncio.create_task(run_worker())
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError as exc:
            stop.set()
            try:
                await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=self.cancellation_grace,
                )
            except TimeoutError as timeout:
                raise OperationTimeout("搜索取消收尾超时") from timeout
            raise OperationCancelled("搜索已取消") from exc

    async def read(self, request: ReadRequest) -> ReadResult:
        return await asyncio.to_thread(self._read_sync, request)

    def _search_sync(
        self,
        request: SearchRequest,
        stop: threading.Event,
    ) -> SearchResult:
        root = Path(request.root)
        if not root.is_dir():
            raise ValidationError("搜索根目录不存在或不是目录")
        budget = request.budget or self.default_budget
        expression = _compile_pattern(request)
        started = time.monotonic()
        deadline = started + budget.timeout
        state = _ScanState()
        reason = ""
        try:
            for path, relative in _iter_files(root, request, stop, deadline):
                if is_sensitive_selector(relative):
                    continue
                state.scanned_files += 1
                if state.scanned_files > budget.max_files:
                    state.scanned_files = budget.max_files
                    raise _ScanStopped("file_limit")
                file_reason = _collect_search_file(
                    path,
                    relative,
                    request,
                    expression,
                    budget,
                    deadline,
                    stop,
                    state,
                )
                if file_reason:
                    raise _ScanStopped(file_reason)
        except _ScanStopped as exc:
            reason = exc.reason
        elapsed = time.monotonic() - started
        return SearchResult(
            hits=tuple(sorted(state.hits[: budget.max_results])),
            truncated=bool(reason),
            reason=reason,
            scanned_files=state.scanned_files,
            scanned_bytes=state.scanned_bytes,
            elapsed=elapsed,
        )

    def _read_sync(self, request: ReadRequest) -> ReadResult:
        path = Path(request.path)
        if is_sensitive_selector(path.as_posix()):
            raise ValidationError("敏感文件不可读取")
        if not path.is_file():
            raise ValidationError("文件不存在或路径不是文件")
        budget = request.budget or self.default_read_budget
        content, bytes_read, reason = _read_prefix(path, budget)
        if len(content) > budget.max_chars:
            content = content[: budget.max_chars]
            reason = reason or "character_limit"
        lines = content.splitlines()
        if len(lines) > budget.max_lines:
            content = "\n".join(lines[: budget.max_lines])
            reason = "line_limit"
        truncated = bool(reason) or path.stat().st_size > bytes_read
        if truncated and not reason:
            reason = "byte_limit"
        return ReadResult(
            content=content,
            truncated=truncated,
            reason=reason,
            lines=len(content.splitlines()),
            bytes_read=bytes_read,
        )


def _read_prefix(path: Path, budget: ReadBudget) -> tuple[str, int, str]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    parts: list[str] = []
    bytes_read = 0
    reason = ""
    with path.open("rb") as stream:
        while bytes_read < budget.max_bytes:
            chunk = stream.read(min(_CHUNK_SIZE, budget.max_bytes - bytes_read))
            if not chunk:
                break
            bytes_read += len(chunk)
            parts.append(decoder.decode(chunk))
            visible = "".join(parts)
            if len(visible) >= budget.max_chars:
                reason = "character_limit"
                break
            if visible.count("\n") >= budget.max_lines:
                reason = "line_limit"
                break
        else:
            reason = "byte_limit"
        if not reason:
            parts.append(decoder.decode(b"", final=True))
    return "".join(parts), bytes_read, reason


def _compile_pattern(request: SearchRequest) -> re.Pattern[str] | None:
    if request.kind is SearchKind.GLOB:
        return None
    try:
        return re.compile(request.pattern)
    except re.error as exc:
        raise ValidationError(f"正则非法: {exc}") from exc


def _checkpoint(stop: threading.Event, deadline: float) -> str:
    if stop.is_set():
        return "cancelled"
    if time.monotonic() >= deadline:
        return "timeout"
    return ""


def _iter_files(
    root: Path,
    request: SearchRequest,
    stop: threading.Event,
    deadline: float,
) -> Iterator[tuple[Path, str]]:
    stack = [root]
    while stack:
        reason = _checkpoint(stop, deadline)
        if reason:
            raise _ScanStopped(reason)
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            reason = _checkpoint(stop, deadline)
            if reason:
                raise _ScanStopped(reason)
            relative = _relative(Path(entry.path), root)
            if _is_ignored(relative, entry.name, request.additional_ignores):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path), relative
            except OSError:
                continue


def _collect_search_file(
    path: Path,
    relative: str,
    request: SearchRequest,
    expression: re.Pattern[str] | None,
    budget: SearchBudget,
    deadline: float,
    stop: threading.Event,
    state: _ScanState,
) -> str:
    if request.kind is SearchKind.GLOB:
        if _matches_glob(relative, request.pattern):
            state.hits.append(relative)
        return "result_limit" if len(state.hits) >= budget.max_results else ""
    if not _matches_file_filter(relative, request.file_glob):
        return ""
    file_hits, read_bytes, reason = _grep_file(
        path,
        relative,
        expression,
        budget,
        budget.max_total_bytes - state.scanned_bytes,
        budget.max_results - len(state.hits),
        deadline,
        stop,
    )
    state.scanned_bytes += read_bytes
    state.hits.extend(file_hits)
    return reason


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_ignored(relative: str, name: str, additional: tuple[str, ...]) -> bool:
    if name in _IGNORED_NAMES or name.startswith(".venv"):
        return True
    parts = PurePosixPath(relative).parts
    if len(parts) >= 2 and parts[0] == ".novacode" and parts[1] == "worktrees":
        return True
    return any(
        fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in additional
    )


def _matches_glob(relative: str, pattern: str) -> bool:
    path = PurePosixPath(relative)
    if "/" not in pattern and "/" in relative:
        return False
    if path.match(pattern):
        return True
    return pattern.startswith("**/") and path.match(pattern[3:])


def _matches_file_filter(relative: str, pattern: str) -> bool:
    return not pattern or _matches_glob(relative, pattern)


def _grep_file(
    path: Path,
    relative: str,
    expression: re.Pattern[str] | None,
    budget: SearchBudget,
    remaining_total: int,
    remaining_results: int,
    deadline: float,
    stop: threading.Event,
) -> tuple[list[str], int, str]:
    if expression is None:
        raise ValidationError("grep 缺少正则表达式")
    if remaining_total <= 0:
        return [], 0, "total_bytes"
    with path.open("rb") as stream:
        hits, read_bytes, reason, tail, line_number = _scan_grep_stream(
            stream,
            expression,
            relative,
            budget,
            remaining_total,
            remaining_results,
            deadline,
            stop,
        )
    if reason:
        return hits, read_bytes, reason
    if tail:
        _append_matching_line(tail, expression, relative, line_number + 1, hits)
        if len(hits) >= remaining_results:
            return hits, read_bytes, "result_limit"
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = read_bytes
    if read_bytes >= remaining_total and file_size > read_bytes:
        return hits, read_bytes, "total_bytes"
    if read_bytes >= budget.max_file_bytes and file_size > read_bytes:
        return hits, read_bytes, "file_bytes"
    return hits, read_bytes, ""


def _scan_grep_stream(
    stream: BinaryIO,
    expression: re.Pattern[str],
    relative: str,
    budget: SearchBudget,
    remaining_total: int,
    remaining_results: int,
    deadline: float,
    stop: threading.Event,
) -> tuple[list[str], int, str, bytes, int]:
    hits: list[str] = []
    read_bytes = 0
    buffer = bytearray()
    line_number = 0
    while read_bytes < budget.max_file_bytes and read_bytes < remaining_total:
        reason = _checkpoint(stop, deadline)
        if reason:
            return hits, read_bytes, reason, bytes(buffer), line_number
        allowance = min(
            _CHUNK_SIZE,
            budget.max_file_bytes - read_bytes,
            remaining_total - read_bytes,
        )
        chunk = stream.read(allowance)
        if not chunk:
            break
        read_bytes += len(chunk)
        buffer.extend(chunk)
        buffer, line_number, limit_reached = _consume_complete_lines(
            buffer,
            expression,
            relative,
            line_number,
            hits,
            remaining_results,
        )
        if limit_reached:
            return hits, read_bytes, "result_limit", bytes(buffer), line_number
    return hits, read_bytes, "", bytes(buffer), line_number


def _consume_complete_lines(
    buffer: bytearray,
    expression: re.Pattern[str],
    relative: str,
    line_number: int,
    hits: list[str],
    remaining_results: int,
) -> tuple[bytearray, int, bool]:
    while b"\n" in buffer:
        raw_line, _, rest = buffer.partition(b"\n")
        buffer = bytearray(rest)
        line_number += 1
        _append_matching_line(raw_line, expression, relative, line_number, hits)
        if len(hits) >= remaining_results:
            return buffer, line_number, True
    return buffer, line_number, False


def _append_matching_line(
    raw_line: bytes | bytearray,
    expression: re.Pattern[str],
    relative: str,
    line_number: int,
    hits: list[str],
) -> None:
    text = raw_line.decode("utf-8", errors="replace").rstrip("\r")
    if expression.search(text):
        hits.append(f"{relative}:{line_number}:{text}")
