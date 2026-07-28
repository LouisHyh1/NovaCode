"""Expired session cleanup."""

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from novacode.session.reader import load_session
from novacode.session.types import SESSION_ID_RE

logger = logging.getLogger(__name__)


def clean_expired(
    sessions_dir: Path,
    now: datetime,
    max_age: timedelta = timedelta(days=30),
) -> None:
    sessions_dir = Path(sessions_dir)
    for path in sessions_dir.glob("*.jsonl"):
        if SESSION_ID_RE.fullmatch(path.stem) is None:
            continue
        session_dir = sessions_dir / path.stem
        tool_results = session_dir / "tool-results"
        try:
            result = load_session(path)
            activity = result.last_activity or _fallback_activity(path, tool_results)
        except OSError as exc:
            logger.warning("session cleanup scan failed: %s (%s)", path, type(exc).__name__)
            continue
        if now.astimezone(UTC) - activity <= max_age:
            continue
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("session file cleanup failed: %s (%s)", path, type(exc).__name__)
        try:
            if session_dir.exists():
                shutil.rmtree(session_dir)
        except OSError as exc:
            logger.warning("session tools cleanup failed: %s (%s)", session_dir, type(exc).__name__)


async def clean_expired_async(
    sessions_dir: Path,
    now: datetime,
    max_age: timedelta = timedelta(days=30),
) -> None:
    await asyncio.to_thread(clean_expired, sessions_dir, now, max_age)


def _fallback_activity(path: Path, tool_results: Path) -> datetime:
    timestamp = path.stat().st_mtime
    if tool_results.exists():
        timestamp = max(timestamp, tool_results.stat().st_mtime)
    return datetime.fromtimestamp(timestamp, UTC)
