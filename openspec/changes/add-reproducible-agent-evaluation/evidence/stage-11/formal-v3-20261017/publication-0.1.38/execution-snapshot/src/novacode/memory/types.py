"""Long-term memory types."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class MemoryKind(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass(frozen=True)
class MemoryAction:
    action: Literal["create", "update", "delete", "no-op"]
    kind: MemoryKind | None = None
    memory_id: str = ""
    title: str = ""
    summary: str = ""
    content: str = ""
    filename: str = ""


@dataclass(frozen=True)
class MemoryTurn:
    user_content: str
    assistant_content: str


@dataclass(frozen=True)
class ApplyReport:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    rejected: int = 0


class MemoryLockError(RuntimeError):
    pass


class MemoryRecoveryRequired(RuntimeError):  # noqa: N818 - public name fixed by ch09 spec
    pass
