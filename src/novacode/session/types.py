"""Session persistence types."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from novacode.llm import Message

SESSION_ID_RE = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")


class SessionWriteError(OSError):
    pass


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    title: str
    model: str
    last_activity: datetime
    file_size: int
    path: Path


@dataclass
class SessionLoadResult:
    session_id: str
    messages: list[Message]
    model: str
    last_activity: datetime | None
    diagnostics: list[str] = field(default_factory=list)
