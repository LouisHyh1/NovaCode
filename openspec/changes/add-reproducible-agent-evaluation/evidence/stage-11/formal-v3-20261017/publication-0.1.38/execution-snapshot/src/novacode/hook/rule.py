"""Hook 配置编译后的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from novacode.hook.event import Event
from novacode.permission.matcher import Matcher

Payload = dict[str, Any]


class CombineMode(StrEnum):
    ALL_OF = "all_of"
    ANY_OF = "any_of"


class ActionType(StrEnum):
    SHELL = "shell"
    PROMPT = "prompt"
    HTTP = "http"
    SUBAGENT = "subagent"


@dataclass(frozen=True)
class AtomCondition:
    field: str
    matcher: Matcher


@dataclass(frozen=True)
class Condition:
    mode: CombineMode
    atoms: list[AtomCondition]


@dataclass(frozen=True)
class ShellAction:
    command: str
    type: ActionType = field(default=ActionType.SHELL, init=False)


@dataclass(frozen=True)
class PromptAction:
    text: str
    type: ActionType = field(default=ActionType.PROMPT, init=False)


@dataclass(frozen=True)
class HttpAction:
    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    type: ActionType = field(default=ActionType.HTTP, init=False)


@dataclass(frozen=True)
class SubagentAction:
    agent_name: str
    prompt: str
    type: ActionType = field(default=ActionType.SUBAGENT, init=False)


Action = ShellAction | PromptAction | HttpAction | SubagentAction


@dataclass(frozen=True)
class Rule:
    name: str
    event: Event
    action: Action
    condition: Condition | None = None
    only_once: bool = False
    asyncio_mode: bool = False
    timeout_s: float = 30.0
    source: str = ""
