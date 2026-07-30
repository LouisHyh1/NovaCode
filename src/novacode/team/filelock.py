"""基于独占 lock 文件的轻量跨进程锁。"""

import asyncio
import contextlib
import os
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path

LOCK_MAX_RETRIES = 10
LOCK_STALE_AFTER = 10.0
LOCK_BACKOFF_MIN = 0.005
LOCK_BACKOFF_MAX = 0.1


@asynccontextmanager
async def acquire(path: str | Path):
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    for attempt in range(LOCK_MAX_RETRIES):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > LOCK_STALE_AFTER
            except FileNotFoundError:
                stale = False
            if stale:
                with contextlib.suppress(FileNotFoundError):
                    lock_path.unlink()
                continue
            if attempt + 1 < LOCK_MAX_RETRIES:
                await asyncio.sleep(random.uniform(LOCK_BACKOFF_MIN, LOCK_BACKOFF_MAX))
    if fd is None:
        raise TimeoutError(f"文件锁获取失败: {lock_path}")
    os.close(fd)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()
