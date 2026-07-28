"""Recoverable session listing."""

from pathlib import Path

from novacode.session.reader import load_session
from novacode.session.types import SESSION_ID_RE, SessionInfo

_TITLE_LIMIT = 80


def list_sessions(sessions_dir: Path) -> list[SessionInfo]:
    sessions: list[SessionInfo] = []
    for path in Path(sessions_dir).glob("*.jsonl"):
        if SESSION_ID_RE.fullmatch(path.stem) is None:
            continue
        try:
            result = load_session(path)
            file_size = path.stat().st_size
        except OSError:
            continue
        if not result.messages or result.last_activity is None or not result.model:
            continue
        title = next(
            (
                " ".join(message.content.splitlines()).strip()[:_TITLE_LIMIT]
                for message in result.messages
                if message.role == "user" and message.content.strip()
            ),
            "（无用户消息）",
        )
        sessions.append(
            SessionInfo(
                session_id=path.stem,
                title=title,
                model=result.model,
                last_activity=result.last_activity,
                file_size=file_size,
                path=path,
            )
        )
    return sorted(sessions, key=lambda session: session.last_activity, reverse=True)
