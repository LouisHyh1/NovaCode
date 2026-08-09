"""单轮 Agent 执行的核心类型化合同。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """一次执行所需的会话视图、输入、模式和取消句柄。"""

    conversation: object
    user_input: str
    mode: int = 0
    cancel: asyncio.Event = field(default_factory=asyncio.Event, compare=False)


@dataclass(frozen=True, slots=True)
class TextTurnEvent:
    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True, slots=True)
class ToolTurnEvent:
    name: str
    args: str
    phase: str
    result: str = ""
    is_error: bool = False
    kind: Literal["tool"] = "tool"


@dataclass(frozen=True, slots=True)
class ApprovalTurnEvent:
    name: str
    args: str
    reason: str
    response: object
    kind: Literal["approval"] = "approval"


@dataclass(frozen=True, slots=True)
class UsageTurnEvent:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    kind: Literal["usage"] = "usage"


@dataclass(frozen=True, slots=True)
class DoneTurnEvent:
    memory_turn: object | None = None
    kind: Literal["done"] = "done"


@dataclass(frozen=True, slots=True)
class CancelledTurnEvent:
    reason: str = "cancelled"
    kind: Literal["cancelled"] = "cancelled"


@dataclass(frozen=True, slots=True)
class ErrorTurnEvent:
    error_category: str
    message: str
    error: Exception
    kind: Literal["error"] = "error"


type TurnEvent = (
    TextTurnEvent
    | ToolTurnEvent
    | ApprovalTurnEvent
    | UsageTurnEvent
    | DoneTurnEvent
    | CancelledTurnEvent
    | ErrorTurnEvent
)


class TurnEngine(Protocol):
    """通过异步迭代器暴露一轮执行，不规定具体 Provider 或 UI。"""

    def run(self, request: TurnRequest) -> AsyncIterator[TurnEvent]: ...
