"""Team 作用域身份与协作领域类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NewType

TeamId = NewType("TeamId", str)
AgentId = NewType("AgentId", str)
MemberName = NewType("MemberName", str)


@dataclass(frozen=True, slots=True)
class AgentAddress:
    team_id: TeamId
    member_name: MemberName


@dataclass(frozen=True, slots=True)
class AgentRun:
    run_id: str
    agent_id: AgentId
    name: str


@dataclass(frozen=True, slots=True)
class TeamTask:
    task_id: str
    title: str
    description: str = ""
    status: str = "pending"
    assignee: AgentId | None = None
    historical_assignee: str = ""
    blocked_by: frozenset[str] = field(default_factory=frozenset)
    blocks: frozenset[str] = field(default_factory=frozenset)
    created_at: int = 0
    updated_at: int = 0
    is_ready: bool = True


@dataclass(frozen=True, slots=True)
class TeamMember:
    address: AgentAddress
    agent_id: AgentId
    active: bool | None = None
    agent_type: str = ""
    model: str = ""
    worktree_path: str = ""
    branch: str = ""
    backend_type: str = "in-process"
    pane_id: str = ""
    plan_mode_required: bool = False
    session_dir: str = ""


@dataclass(frozen=True, slots=True)
class TeamState:
    team_id: TeamId
    name: str
    members: tuple[TeamMember, ...] = ()
    schema_version: int = 1
    revision: int = 0
    sanitized_name: str = ""
    lead_agent_id: AgentId = AgentId("lead")
    backend: str = "in-process"
    description: str = ""
    created_at: float = 0.0
    permission_mode: str = "default"


@dataclass(frozen=True, slots=True)
class TeamTaskGraph:
    team_id: TeamId
    tasks: tuple[TeamTask, ...] = ()
    schema_version: int = 1
    revision: int = 0


@dataclass(frozen=True, slots=True)
class MailboxMessage:
    message_id: str
    sender: AgentId
    text: str


class TeamMemberDirectory:
    """只在单个 Team 内维护 Member Name 到 Agent ID 的映射。"""

    def __init__(self, team_id: TeamId) -> None:
        self.team_id = team_id
        self._members: dict[MemberName, AgentId] = {}

    def register(self, name: MemberName, agent_id: AgentId) -> AgentAddress:
        if name in self._members:
            raise ValueError(f"Team 成员名已存在: {name}")
        self._members[name] = agent_id
        return AgentAddress(self.team_id, name)

    def resolve(self, name: MemberName) -> AgentId | None:
        return self._members.get(name)
