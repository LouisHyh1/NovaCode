"""工具执行期间绑定当前 Agent 上下文。"""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novacode.agent import Agent
    from novacode.conversation import Conversation


@dataclass(frozen=True)
class ExecutionContext:
    agent: "Agent"
    conversation: "Conversation"


_CURRENT: ContextVar[ExecutionContext | None] = ContextVar("novacode_agent_context", default=None)


def bind(context: ExecutionContext) -> Token:
    return _CURRENT.set(context)


def reset(token: Token) -> None:
    _CURRENT.reset(token)


def current() -> ExecutionContext | None:
    return _CURRENT.get()
