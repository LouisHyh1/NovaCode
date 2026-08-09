"""运行时核心依赖的向内 Port。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Protocol


class ProviderPort(Protocol):
    def stream(self, request: object) -> AsyncIterator[object]: ...


class ToolExecutorPort(Protocol):
    async def execute(self, name: str, arguments: str) -> object: ...


class PermissionPolicyPort(Protocol):
    async def authorize(self, name: str, arguments: str, mode: int) -> object: ...


class HookDispatcherPort(Protocol):
    async def dispatch(
        self,
        event: str,
        payload: Mapping[str, object],
    ) -> object: ...


class ConversationStorePort(Protocol):
    async def load(self, session_id: str) -> object: ...

    async def save(self, session_id: str, conversation: object) -> None: ...
