"""Team 聚合的异步 Repository 端口。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from novacode.team.domain import (
    AgentId,
    MailboxMessage,
    TeamId,
    TeamState,
    TeamTaskGraph,
)

TeamMutation = Callable[[TeamState], TeamState]
TeamTaskMutation = Callable[[TeamTaskGraph], TeamTaskGraph]


@runtime_checkable
class TeamRepository(Protocol):
    async def load(self, aggregate_id: TeamId) -> TeamState: ...

    async def create(self, state: TeamState) -> TeamState: ...

    async def transact(self, aggregate_id: TeamId, mutation: TeamMutation) -> TeamState: ...


@runtime_checkable
class TeamTaskRepository(Protocol):
    async def load(self, aggregate_id: TeamId) -> TeamTaskGraph: ...

    async def create(self, state: TeamTaskGraph) -> TeamTaskGraph: ...

    async def transact(self, aggregate_id: TeamId, mutation: TeamTaskMutation) -> TeamTaskGraph: ...


@runtime_checkable
class MailboxRepository(Protocol):
    async def append(self, agent_id: AgentId, message: MailboxMessage) -> None: ...

    async def list_messages(self, agent_id: AgentId) -> tuple[MailboxMessage, ...]: ...
