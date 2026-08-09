"""具有跨进程锁与原子发布语义的 JSON 事务存储。"""

from __future__ import annotations

import asyncio
import copy
import ctypes
import json
import os
import secrets
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import cast

from novacode.runtime.errors import CleanupError, OperationTimeout, StateCorruptionError

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]
JsonMutation = Callable[[JsonObject], JsonObject]
JsonValidator = Callable[[JsonObject], None]
ConditionalJsonMutation = Callable[[JsonObject], JsonObject | None]
FaultHook = Callable[[str, Path], None]


@dataclass(frozen=True, slots=True)
class LockOwner:
    pid: int
    created_at: float
    token: str

    def to_json(self) -> bytes:
        value = {"pid": self.pid, "created_at": self.created_at, "token": self.token}
        return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def process_is_alive(pid: int) -> bool:
    """使用平台原生能力保守判断进程是否仍存活。"""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        loader: object = getattr(ctypes, "windll")
        kernel32: object = getattr(loader, "kernel32")
        open_process = cast(
            Callable[[int, bool, int], int],
            getattr(kernel32, "OpenProcess"),
        )
        close_handle = cast(Callable[[int], int], getattr(kernel32, "CloseHandle"))
        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            return False
        close_handle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ProcessFileLock(AbstractAsyncContextManager["ProcessFileLock"]):
    """只有持有匹配 token 的进程才能释放的跨进程文件锁。"""

    def __init__(
        self,
        path: str | Path,
        *,
        acquire_timeout: float = 10.0,
        poll_interval: float = 0.02,
    ) -> None:
        self.path = Path(path)
        self.acquire_timeout = acquire_timeout
        self.poll_interval = poll_interval
        self._owner: LockOwner | None = None

    async def acquire(self) -> None:
        await asyncio.to_thread(self._acquire_sync)

    async def release(self) -> None:
        await asyncio.to_thread(self._release_sync)

    async def __aenter__(self) -> ProcessFileLock:
        await self.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.release()

    def _acquire_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.acquire_timeout
        owner = LockOwner(os.getpid(), time.time(), secrets.token_urlsafe(24))
        while True:
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                self._reclaim_dead_owner()
                if time.monotonic() >= deadline:
                    raise OperationTimeout(f"文件锁获取超时: {self.path}")
                time.sleep(self.poll_interval)
                continue
            try:
                os.write(descriptor, owner.to_json())
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._owner = owner
            return

    def _release_sync(self) -> None:
        owner = self._owner
        if owner is None:
            return
        current = self._read_owner()
        if current is not None and current.token == owner.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self._owner = None

    def _reclaim_dead_owner(self) -> None:
        owner = self._read_owner()
        if owner is None or process_is_alive(owner.pid):
            return
        confirmed = self._read_owner()
        if confirmed is None or confirmed.token != owner.token:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _read_owner(self) -> LockOwner | None:
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        pid = raw.get("pid")
        created_at = raw.get("created_at")
        token = raw.get("token")
        if not isinstance(pid, int) or not isinstance(created_at, int | float):
            return None
        if not isinstance(token, str) or not token:
            return None
        return LockOwner(pid, float(created_at), token)


class AtomicJsonStore:
    """在锁内重读、校验候选并使用 `os.replace` 原子发布。"""

    def __init__(self, path: str | Path, *, fault_hook: FaultHook | None = None) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        self.fault_hook = fault_hook

    async def read(self, default: JsonObject) -> JsonObject:
        return await asyncio.to_thread(self._read_sync, default)

    async def transact(
        self,
        default: JsonObject,
        mutation: JsonMutation,
        validator: JsonValidator | None = None,
    ) -> JsonObject:
        def always_update(current: JsonObject) -> JsonObject:
            return mutation(current)

        return await self.update_if(default, always_update, validator)

    async def update_if(
        self,
        default: JsonObject,
        mutation: ConditionalJsonMutation,
        validator: JsonValidator | None = None,
    ) -> JsonObject:
        async with ProcessFileLock(self.lock_path):
            self._fault("after_lock")
            return await asyncio.to_thread(
                self._update_if_sync,
                default,
                mutation,
                validator,
            )

    def _update_if_sync(
        self,
        default: JsonObject,
        mutation: ConditionalJsonMutation,
        validator: JsonValidator | None,
    ) -> JsonObject:
        current = self._read_sync(default)
        candidate = mutation(copy.deepcopy(current))
        if candidate is None:
            return current
        if validator is not None:
            validator(candidate)
        self._publish_sync(candidate)
        return candidate

    def _read_sync(self, default: JsonObject) -> JsonObject:
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return copy.deepcopy(default)
        except (OSError, json.JSONDecodeError) as exc:
            raise StateCorruptionError(str(self.path), detail=str(exc)) from exc
        if not isinstance(raw, dict) or not _is_json_object(raw):
            raise StateCorruptionError(str(self.path), detail="JSON 顶层或成员类型无效")
        return cast(JsonObject, raw)

    def _publish_sync(self, candidate: JsonObject) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        transaction_id = uuid.uuid4().hex
        temporary = self.path.with_name(f".{self.path.name}.{transaction_id}.tmp")
        self._fault("before_candidate_write")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(candidate, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._fault("after_candidate_write")
            self._fault("before_publish")
            os.replace(temporary, self.path)
            self._fault("after_publish")
            _fsync_directory(self.path.parent)
        finally:
            if temporary.exists():
                try:
                    self._fault("cleanup")
                    temporary.unlink()
                except OSError as exc:
                    raise CleanupError(
                        f"事务临时文件清理失败: {temporary}",
                        residual_path=str(temporary),
                    ) from exc

    def _fault(self, phase: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(phase, self.path)


def _is_json_object(value: dict[object, object]) -> bool:
    return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return _is_json_object(value)
    return False


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
