"""Agent 与 Team 子系统之间的依赖反转接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class TeamSpawnRequest:
    team_name: str
    member_name: str
    prompt: str
    description: str
    subagent_type: str = ""
    model: str = ""
    plan_mode_required: bool = False
    caller_agent: Any = None
    caller_conversation: Any = None


@dataclass
class TeammateContext:
    team_name: str
    member_name: str
    agent_id: str
    backend_type: str
    mailbox: Any
    team_manager: Any


class TeamHook(Protocol):
    async def spawn_teammate(self, request: TeamSpawnRequest) -> str: ...
