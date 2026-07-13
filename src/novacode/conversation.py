"""Single-session conversation history."""

import copy
import threading

from novacode.llm import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER, Message, ToolCall, ToolResult


class Conversation:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        with self._lock:
            self._messages.append(Message(role=ROLE_USER, content=text))

    def add_assistant(self, text: str) -> None:
        with self._lock:
            self._messages.append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        with self._lock:
            self._messages.append(
                Message(role=ROLE_ASSISTANT, content=text, tool_calls=copy.deepcopy(calls))
            )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        with self._lock:
            self._messages.append(Message(role=ROLE_TOOL, tool_results=copy.deepcopy(results)))

    def messages(self) -> list[Message]:
        with self._lock:
            return copy.deepcopy(self._messages)

    def replace_history(self, msgs: list[Message]) -> None:
        if msgs is None:
            raise TypeError("msgs cannot be None")
        with self._lock:
            self._messages = copy.deepcopy(msgs)

    def length(self) -> int:
        with self._lock:
            return len(self._messages)

    def last_role(self) -> str:
        with self._lock:
            return self._messages[-1].role if self._messages else ""
