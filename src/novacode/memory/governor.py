"""Gated background governance for filesystem memories."""

import asyncio
import errno
import logging
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

from novacode.memory.prompts import build_governance_prompt
from novacode.memory.store import MemoryStore
from novacode.memory.types import ApplyReport, MemoryAction, MemoryKind
from novacode.session import SessionInfo, list_sessions

logger = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=10)
GOVERNANCE_INTERVAL = timedelta(hours=24)
STALE_LOCK_SECONDS = 60 * 60

RestrictedAgent = Callable[..., Awaitable[list[MemoryAction]]]
Notifier = Callable[[str], None]


class _ConsolidationLock:
    """Non-blocking OS lock whose file mtime records the last successful run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None
        self._original_content: bytes | None = None
        self._original_times_ns: tuple[int, int] | None = None
        self._owned = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        if existed:
            stat = self.path.stat()
            self._original_times_ns = (stat.st_atime_ns, stat.st_mtime_ns)
            self._original_content = self.path.read_bytes()
        else:
            self._original_content = None
            self._original_times_ns = None

        file = self.path.open("a+b")
        if not _try_os_lock(file):
            file.close()
            return False

        content = self._original_content or b""
        pid = _parse_pid(content)
        age = time.time() - (
            self._original_times_ns[1] / 1_000_000_000
            if self._original_times_ns is not None
            else time.time()
        )
        if pid is not None:
            status = _pid_status(pid)
            if status is True or (status is None and age <= STALE_LOCK_SECONDS):
                _release_os_lock(file)
                file.close()
                if not existed:
                    self.path.unlink(missing_ok=True)
                return False

        file.seek(0)
        file.truncate()
        file.write(str(os.getpid()).encode("ascii"))
        file.flush()
        os.fsync(file.fileno())
        self._file = file
        self._owned = True
        return True

    def release(self, *, success: bool) -> None:
        if not self._owned or self._file is None:
            return
        file = self._file
        try:
            if success:
                file.seek(0)
                file.truncate()
                file.write(b"\n")
                file.flush()
                os.fsync(file.fileno())
                now_ns = time.time_ns()
                os.utime(self.path, ns=(now_ns, now_ns))
            elif self._original_content is not None:
                file.seek(0)
                file.truncate()
                file.write(self._original_content)
                file.flush()
                os.fsync(file.fileno())
                assert self._original_times_ns is not None
                os.utime(self.path, ns=self._original_times_ns)
        finally:
            _release_os_lock(file)
            file.close()
            self._file = None
            self._owned = False
            if not success and self._original_content is None:
                self.path.unlink(missing_ok=True)


class MemoryGovernor:
    def __init__(
        self,
        sessions_dir: Path,
        stores: tuple[MemoryStore, ...],
        run_restricted_agent: RestrictedAgent,
        notify: Notifier,
    ) -> None:
        self.sessions_dir = Path(sessions_dir)
        self.stores = tuple(sorted(stores, key=_store_rank))
        self.run_restricted_agent = run_restricted_agent
        self.notify = notify
        self.lock_path = self.sessions_dir.parent / ".consolidate-lock"
        self._last_scan_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def last_scan_at(self) -> datetime | None:
        return self._last_scan_at

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def maybe_schedule(self, now: datetime) -> bool:
        if self._last_scan_at is not None and now - self._last_scan_at < SCAN_INTERVAL:
            return False
        self._last_scan_at = now

        targets = tuple(store for store in self.stores if store.directory.is_dir())
        if not targets:
            return False
        if self.lock_path.exists():
            try:
                last_success = datetime.fromtimestamp(
                    self.lock_path.stat().st_mtime, tz=now.tzinfo or UTC
                )
            except OSError:
                return False
            if now - last_success < GOVERNANCE_INTERVAL:
                return False
        try:
            sessions = tuple(list_sessions(self.sessions_dir))
        except Exception as exc:
            logger.warning("memory governance session scan failed: %s", type(exc).__name__)
            return False
        if len(sessions) < 5:
            return False

        lock = _ConsolidationLock(self.lock_path)
        if not lock.acquire():
            return False
        try:
            self._task = asyncio.create_task(self._run(lock, targets, sessions))
        except Exception:
            lock.release(success=False)
            raise
        return True

    async def wait(self) -> None:
        task = self._task
        if task is not None:
            await task

    async def close(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(
        self,
        lock: _ConsolidationLock,
        targets: tuple[MemoryStore, ...],
        sessions: tuple[SessionInfo, ...],
    ) -> None:
        success = False
        totals = ApplyReport()
        try:
            async with AsyncExitStack() as stack:
                for store in targets:
                    await stack.enter_async_context(store.locked(create=False))
                for store in targets:
                    indexes = tuple(target.read_index_locked() for target in targets)
                    actions = await self.run_restricted_agent(
                        sessions=sessions,
                        indexes=indexes,
                        target_directory=store.directory.resolve(),
                        allowed_kinds=store.allowed_kinds,
                        prompt=build_governance_prompt(
                            now=datetime.now(UTC), target=str(store.directory.resolve())
                        ),
                    )
                    if not isinstance(actions, list):
                        raise TypeError("restricted governance agent must return a list")
                    report = store.apply_locked(actions)
                    totals = _add_reports(totals, report)
            success = True
            self._notify(_notice("completed", totals))
        except asyncio.CancelledError:
            self._notify(_notice("failed", totals))
            raise
        except Exception as exc:
            logger.warning("memory governance failed: %s", type(exc).__name__)
            self._notify(_notice("failed", totals))
        finally:
            lock.release(success=success)

    def _notify(self, notice: str) -> None:
        try:
            self.notify(notice)
        except Exception as exc:
            logger.warning("memory governance notification failed: %s", type(exc).__name__)


def _notice(status: str, report: ApplyReport) -> str:
    return (
        f"memory governance {status}: create={report.created} "
        f"update={report.updated} delete={report.deleted}"
    )


def _add_reports(left: ApplyReport, right: ApplyReport) -> ApplyReport:
    return ApplyReport(
        created=left.created + right.created,
        updated=left.updated + right.updated,
        deleted=left.deleted + right.deleted,
        rejected=left.rejected + right.rejected,
    )


def _store_rank(store: MemoryStore) -> int:
    if store.allowed_kinds & {MemoryKind.USER, MemoryKind.FEEDBACK}:
        return 0
    return 1


def _parse_pid(content: bytes) -> int | None:
    try:
        text = content.decode("ascii").strip()
        return int(text) if text else None
    except (UnicodeDecodeError, ValueError):
        return None


def _pid_status(pid: int) -> bool | None:
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


def _try_os_lock(file) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _release_os_lock(file) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
    except OSError:
        logger.warning("failed to release memory governance OS lock")
