"""Single-session conversation history."""

import copy
import threading
from collections.abc import Callable

from novacode.llm import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER, Message, ToolCall, ToolResult

BeforeAppend = Callable[[Message], None]
BeforeReplace = Callable[[list[Message]], None]


class Conversation:
    def __init__(
        self,
        before_append: BeforeAppend | None = None,
        before_replace: BeforeReplace | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._messages: list[Message] = []
        self._before_append = before_append
        self._before_replace = before_replace

    @classmethod
    def from_messages(
        cls,
        messages: list[Message],
        before_append: BeforeAppend | None = None,
        before_replace: BeforeReplace | None = None,
    ) -> "Conversation":
        conv = cls(before_append=before_append, before_replace=before_replace)
        conv._messages = copy.deepcopy(messages)
        return conv

    def add_user(self, text: str) -> None:
        self._append(Message(role=ROLE_USER, content=text))

    def add_assistant(self, text: str) -> None:
        self._append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        self._append(Message(role=ROLE_ASSISTANT, content=text, tool_calls=copy.deepcopy(calls)))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self._append(Message(role=ROLE_TOOL, tool_results=copy.deepcopy(results)))

    def _append(self, message: Message) -> None:
        with self._lock:
            if self._before_append is not None:
                self._before_append(message)
            self._messages.append(copy.deepcopy(message))

    def messages(self) -> list[Message]:
        with self._lock:
            return copy.deepcopy(self._messages)

    def replace_history(self, msgs: list[Message]) -> None:
        if msgs is None:
            raise TypeError("msgs cannot be None")
        with self._lock:
            if self._before_replace is not None:
                self._before_replace(msgs)
            self._messages = copy.deepcopy(msgs)

    def length(self) -> int:
        with self._lock:
            return len(self._messages)

    def last_role(self) -> str:
        with self._lock:
            return self._messages[-1].role if self._messages else ""
