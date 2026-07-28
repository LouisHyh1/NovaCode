"""Filesystem-backed long-term memory store."""

import asyncio
import copy
import errno
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novacode.memory.types import (
    ApplyReport,
    MemoryAction,
    MemoryKind,
    MemoryLockError,
    MemoryRecoveryRequired,
)

INDEX_NAME = "MEMORY.md"
JOURNAL_NAME = ".memory-transaction.json"
LOCK_NAME = ".memory-write.lock"
MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25 * 1024
STALE_LOCK_SECONDS = 60 * 60

_INDEX_ENTRY_RE = re.compile(
    r"^- \[(user|feedback|project|reference)\] \[(.+)\]\(([^)]+)\) — (.*) \(id: ([^)]+)\)$"
)


@dataclass
class _Entry:
    memory_id: str
    kind: MemoryKind
    title: str
    summary: str
    content: str
    filename: str
    created: str
    updated: str


class _ProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.owned = False

    def acquire(self) -> bool:
        for _ in range(3):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                if not self._reclaimable():
                    return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    return False
                continue
            with os.fdopen(descriptor, "w", encoding="ascii") as file:
                file.write(str(os.getpid()))
                file.flush()
                os.fsync(file.fileno())
            self.owned = True
            return True
        return False

    def release(self) -> None:
        if not self.owned:
            return
        self.owned = False
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _reclaimable(self) -> bool:
        try:
            pid = int(self.path.read_text(encoding="ascii").strip())
            age = time.time() - self.path.stat().st_mtime
        except (OSError, ValueError):
            return False
        status = _pid_alive(pid)
        if status is True:
            return False
        if status is False:
            return True
        return age > STALE_LOCK_SECONDS


class MemoryStore:
    def __init__(self, directory: Path, allowed_kinds: frozenset[MemoryKind]) -> None:
        self.directory = Path(directory)
        self.allowed_kinds = allowed_kinds
        self._lock = asyncio.Lock()
        self._process_lock = _ProcessLock(self.directory / LOCK_NAME)
        self._recovery_required = False

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @asynccontextmanager
    async def locked(self, *, create: bool = True) -> AsyncIterator[None]:
        await self._lock.acquire()
        try:
            if create:
                self.directory.mkdir(parents=True, exist_ok=True)
            if not self.directory.is_dir():
                raise MemoryLockError(f"memory directory does not exist: {self.directory}")
            acquired = await asyncio.to_thread(self._process_lock.acquire)
            if not acquired:
                raise MemoryLockError(f"memory directory is locked: {self.directory}")
            try:
                yield
            finally:
                await asyncio.to_thread(self._process_lock.release)
        finally:
            self._lock.release()

    def read_index_locked(self) -> str:
        self._require_locked()
        self.recover_locked()
        path = self.directory / INDEX_NAME
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        self._check_capacity(text)
        self._load_entries(text)
        return text

    def render_index_locked(self) -> str:
        return self.read_index_locked()

    def apply_locked(self, actions: list[MemoryAction]) -> ApplyReport:
        self._require_locked()
        self.recover_locked()
        if self._recovery_required:
            raise MemoryRecoveryRequired(f"memory recovery required: {self.directory}")

        index_path = self.directory / INDEX_NAME
        old_index = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        original = self._load_entries(old_index)
        entries = copy.deepcopy(original)
        counts = {"create": 0, "update": 0, "delete": 0, "rejected": 0}
        accepted_changes = 0
        order = {"delete": 0, "update": 1, "create": 2, "no-op": 3}

        for action in sorted(actions, key=lambda item: order.get(item.action, 99)):
            if action.action == "no-op":
                continue
            before = copy.deepcopy(entries)
            try:
                changed = self._apply_action(entries, action)
                candidate_index = self._render_index(entries)
                self._check_capacity(candidate_index)
            except (MemoryRecoveryRequired, OSError):
                raise
            except (TypeError, ValueError):
                entries = before
                counts["rejected"] += 1
                continue
            if changed:
                counts[action.action] += 1
                accepted_changes += 1

        if accepted_changes == 0:
            return ApplyReport(rejected=counts["rejected"])

        new_index = self._render_index(entries)
        if entries == original and new_index == old_index:
            return ApplyReport(rejected=counts["rejected"])

        note_writes: dict[str, str] = {}
        for entry in entries.values():
            rendered = self._render_note(entry)
            path = self.directory / entry.filename
            if not path.exists() or path.read_text(encoding="utf-8") != rendered:
                note_writes[entry.filename] = rendered
        deletes = sorted(
            {entry.filename for entry in original.values()}
            - set(note_writes)
            - {entry.filename for entry in entries.values()}
        )
        self._commit(note_writes, new_index, deletes)
        return ApplyReport(
            created=counts["create"],
            updated=counts["update"],
            deleted=counts["delete"],
            rejected=counts["rejected"],
        )

    def recover_locked(self) -> None:
        self._require_locked()
        if self._recovery_required:
            raise MemoryRecoveryRequired(f"memory recovery required: {self.directory}")
        journal_path = self.directory / JOURNAL_NAME
        if not journal_path.exists():
            self._cleanup_orphan_temps()
            return
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            self._roll_forward(journal)
        except MemoryRecoveryRequired:
            self._recovery_required = True
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._recovery_required = True
            raise MemoryRecoveryRequired(f"invalid memory journal: {self.directory}") from exc

    def _apply_action(self, entries: dict[str, _Entry], action: MemoryAction) -> bool:
        if action.action not in {"create", "update", "delete"}:
            raise ValueError("invalid action")
        if not isinstance(action.kind, MemoryKind) or action.kind not in self.allowed_kinds:
            raise ValueError("invalid memory route")

        if action.action == "create":
            memory_id = action.memory_id or str(uuid.uuid4())
            _validate_uuid(memory_id)
            filename = action.filename or f"{memory_id}.md"
            _validate_filename(filename)
            if memory_id in entries or any(
                entry.filename == filename for entry in entries.values()
            ):
                raise ValueError("duplicate memory")
            title = _single_line(action.title, "title")
            summary = _single_line(action.summary, "summary")
            content = _content(action.content)
            now = datetime.now(UTC).isoformat()
            entries[memory_id] = _Entry(
                memory_id,
                action.kind,
                title,
                summary,
                content,
                filename,
                now,
                now,
            )
            return True

        _validate_uuid(action.memory_id)
        entry = entries.get(action.memory_id)
        if entry is None or entry.kind != action.kind:
            raise ValueError("memory target not found")
        if action.filename and action.filename != entry.filename:
            raise ValueError("memory filename mismatch")
        if action.action == "delete":
            del entries[action.memory_id]
            return True

        entry.title = _single_line(action.title, "title") if action.title else entry.title
        entry.summary = _single_line(action.summary, "summary") if action.summary else entry.summary
        entry.content = _content(action.content) if action.content else entry.content
        entry.updated = datetime.now(UTC).isoformat()
        return True

    def _load_entries(self, index: str) -> dict[str, _Entry]:
        if not index:
            return {}
        if not index.startswith("# Memory Index\n"):
            self._recovery_error("invalid memory index")
        entries: dict[str, _Entry] = {}
        for line in index.splitlines()[2:]:
            match = _INDEX_ENTRY_RE.fullmatch(line)
            if match is None:
                self._recovery_error("invalid memory index entry")
            kind_value, title, filename, summary, memory_id = match.groups()
            _validate_filename(filename)
            _validate_uuid(memory_id)
            note_path = self.directory / filename
            if not note_path.is_file():
                self._recovery_error("memory index references missing note")
            entry = self._parse_note(note_path, summary)
            if (
                entry.memory_id != memory_id
                or entry.kind != MemoryKind(kind_value)
                or entry.title != title
                or memory_id in entries
            ):
                self._recovery_error("memory index metadata mismatch")
            entries[memory_id] = entry
        return entries

    def _parse_note(self, path: Path, summary: str) -> _Entry:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            self._recovery_error("invalid memory note")
        try:
            _, frontmatter, content = text.split("---\n", 2)
            metadata = {
                key: json.loads(value.strip())
                for line in frontmatter.splitlines()
                for key, value in [line.split(":", 1)]
            }
            entry = _Entry(
                memory_id=metadata["id"],
                kind=MemoryKind(metadata["type"]),
                title=metadata["title"],
                summary=summary,
                content=content.rstrip("\n"),
                filename=path.name,
                created=metadata["created"],
                updated=metadata["updated"],
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise MemoryRecoveryRequired(f"invalid memory note: {path}") from exc
        return entry

    @staticmethod
    def _render_note(entry: _Entry) -> str:
        fields = {
            "id": entry.memory_id,
            "type": entry.kind.value,
            "title": entry.title,
            "created": entry.created,
            "updated": entry.updated,
        }
        frontmatter = "\n".join(
            f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()
        )
        return f"---\n{frontmatter}\n---\n{entry.content.rstrip()}\n"

    @staticmethod
    def _render_index(entries: dict[str, _Entry]) -> str:
        if not entries:
            return "# Memory Index\n"
        lines = [
            f"- [{entry.kind.value}] [{entry.title}]({entry.filename}) — "
            f"{entry.summary} (id: {entry.memory_id})"
            for entry in sorted(entries.values(), key=lambda item: item.filename)
        ]
        return "# Memory Index\n\n" + "\n".join(lines)

    @staticmethod
    def _check_capacity(index: str) -> None:
        if len(index.splitlines()) > MAX_INDEX_LINES:
            raise ValueError("memory index exceeds 200 lines")
        if len(index.encode("utf-8")) > MAX_INDEX_BYTES:
            raise ValueError("memory index exceeds 25KB")

    def _commit(self, note_writes: dict[str, str], index: str, deletes: list[str]) -> None:
        transaction_id = uuid.uuid4().hex
        writes: list[dict[str, str]] = []
        for position, (filename, content) in enumerate(sorted(note_writes.items())):
            temp = f".memory-txn-{transaction_id}-note-{position}.tmp"
            self._write_fsync(self.directory / temp, content.encode("utf-8"))
            writes.append({"target": filename, "temp": temp, "sha256": _sha(content.encode())})

        index_temp = f".memory-txn-{transaction_id}-index.tmp"
        index_bytes = index.encode("utf-8")
        self._write_fsync(self.directory / index_temp, index_bytes)
        journal = {
            "version": 1,
            "txn_id": transaction_id,
            "writes": writes,
            "index": {"target": INDEX_NAME, "temp": index_temp, "sha256": _sha(index_bytes)},
            "deletes": deletes,
        }
        journal_temp = self.directory / f".memory-transaction.{transaction_id}.tmp"
        self._write_fsync(
            journal_temp,
            json.dumps(journal, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )
        os.replace(journal_temp, self.directory / JOURNAL_NAME)
        self._fsync_directory()
        self._roll_forward(journal)

    def _roll_forward(self, journal: Any) -> None:
        writes, index, deletes, transaction_id = self._validate_journal(journal)
        for item in [*writes, index]:
            target = self.directory / item["target"]
            temp = self.directory / item["temp"]
            expected = item["sha256"]
            if _path_sha(target) == expected:
                continue
            if _path_sha(temp) != expected:
                raise MemoryRecoveryRequired(f"memory transaction data missing: {self.directory}")

        for item in writes:
            target = self.directory / item["target"]
            if _path_sha(target) != item["sha256"]:
                os.replace(self.directory / item["temp"], target)
        self._fsync_directory()

        index_target = self.directory / index["target"]
        if _path_sha(index_target) != index["sha256"]:
            os.replace(self.directory / index["temp"], index_target)
        self._fsync_directory()

        for filename in deletes:
            (self.directory / filename).unlink(missing_ok=True)
        (self.directory / JOURNAL_NAME).unlink(missing_ok=True)
        for path in self.directory.glob(f".memory-txn-{transaction_id}-*.tmp"):
            path.unlink(missing_ok=True)
        self._fsync_directory()

    def _validate_journal(
        self, journal: Any
    ) -> tuple[list[dict[str, str]], dict[str, str], list[str], str]:
        if not isinstance(journal, dict) or journal.get("version") != 1:
            raise MemoryRecoveryRequired("invalid memory journal schema")
        transaction_id = journal.get("txn_id")
        writes = journal.get("writes")
        index = journal.get("index")
        deletes = journal.get("deletes")
        if (
            not isinstance(transaction_id, str)
            or not transaction_id
            or not isinstance(writes, list)
            or not isinstance(index, dict)
            or not isinstance(deletes, list)
        ):
            raise MemoryRecoveryRequired("invalid memory journal schema")
        for item in writes:
            self._validate_manifest_item(item, transaction_id, index=False)
        self._validate_manifest_item(index, transaction_id, index=True)
        for filename in deletes:
            _validate_filename(filename)
        return writes, index, deletes, transaction_id

    @staticmethod
    def _validate_manifest_item(item: Any, transaction_id: str, *, index: bool) -> None:
        if not isinstance(item, dict):
            raise MemoryRecoveryRequired("invalid memory journal item")
        target, temp, digest = item.get("target"), item.get("temp"), item.get("sha256")
        if index:
            if target != INDEX_NAME:
                raise MemoryRecoveryRequired("invalid memory index target")
        else:
            try:
                _validate_filename(target)
            except (TypeError, ValueError) as exc:
                raise MemoryRecoveryRequired("invalid memory note target") from exc
        if (
            not isinstance(temp, str)
            or Path(temp).name != temp
            or not temp.startswith(f".memory-txn-{transaction_id}-")
            or not temp.endswith(".tmp")
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise MemoryRecoveryRequired("invalid memory journal item")

    def _cleanup_orphan_temps(self) -> None:
        for pattern in (".memory-transaction.*.tmp", ".memory-txn-*.tmp"):
            for path in self.directory.glob(pattern):
                path.unlink(missing_ok=True)

    @staticmethod
    def _write_fsync(path: Path, content: bytes) -> None:
        with path.open("wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

    def _fsync_directory(self) -> None:
        try:
            descriptor = os.open(self.directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _require_locked(self) -> None:
        if not self._lock.locked() or not self._process_lock.owned:
            raise MemoryLockError("memory store operation requires both locks")

    def _recovery_error(self, message: str) -> None:
        self._recovery_required = True
        raise MemoryRecoveryRequired(f"{message}: {self.directory}")


def _validate_uuid(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("invalid memory ID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("invalid memory ID") from exc
    if str(parsed) != value.lower():
        raise ValueError("invalid memory ID")


def _validate_filename(value: Any) -> None:
    if (
        not isinstance(value, str)
        or not value
        or Path(value).name != value
        or not value.endswith(".md")
        or value.startswith(".")
        or value == INDEX_NAME
    ):
        raise ValueError("invalid memory filename")


def _single_line(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"invalid memory {label}")
    value = value.strip()
    if label == "title" and any(character in value for character in "[]()"):
        raise ValueError("invalid memory title")
    if label == "summary" and " (id: " in value:
        raise ValueError("invalid memory summary")
    return value


def _content(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid memory content")
    return value.strip()


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _path_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError:
        return ""


def _pid_alive(pid: int) -> bool | None:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno in {errno.ESRCH, errno.EINVAL} or getattr(exc, "winerror", None) == 87:
            return False
        return None
    return True
