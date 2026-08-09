"""Agent Team 的核心数据类型。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class BackendType(StrEnum):
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"


class TeamError(RuntimeError):
    """Team 子系统错误基类。"""


class TeamNotFoundError(TeamError):
    pass


class TeamHasActiveMembersError(TeamError):
    pass


class MemberExistsError(TeamError):
    pass


class MemberNotFoundError(TeamError):
    pass


class MemberHasTasksError(TeamError):
    category = "conflict"

    def __init__(self, member_name: str, task_ids: tuple[str, ...]) -> None:
        self.member_name = member_name
        self.task_ids = task_ids
        super().__init__(f"Team 成员 {member_name} 仍负责未完成任务: {', '.join(task_ids)}")


class InProcessTeammateNoSpawnError(TeamError):
    pass


@dataclass
class TeammateInfo:
    name: str
    agent_id: str
    agent_type: str = ""
    model: str = ""
    worktree_path: str = ""
    branch: str = ""
    backend_type: BackendType = BackendType.IN_PROCESS
    pane_id: str = ""
    is_active: bool | None = None
    plan_mode_required: bool = False
    session_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "model": self.model,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "backend_type": self.backend_type.value,
            "pane_id": self.pane_id,
            "is_active": self.is_active,
            "plan_mode_required": self.plan_mode_required,
            "session_dir": self.session_dir,
        }

    @classmethod
    def from_dict(cls, value: dict) -> TeammateInfo:
        return cls(
            name=str(value.get("name", "")),
            agent_id=str(value.get("agent_id", "")),
            agent_type=str(value.get("agent_type", "")),
            model=str(value.get("model", "")),
            worktree_path=str(value.get("worktree_path", "")),
            branch=str(value.get("branch", "")),
            backend_type=BackendType(value.get("backend_type", BackendType.IN_PROCESS)),
            pane_id=str(value.get("pane_id", "")),
            is_active=value.get("is_active"),
            plan_mode_required=bool(value.get("plan_mode_required", False)),
            session_dir=str(value.get("session_dir", "")),
        )


@dataclass
class Team:
    name: str
    sanitized_name: str
    lead_agent_id: str
    backend: BackendType
    team_id: str = ""
    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    members: list[TeammateInfo] = field(default_factory=list)
    config_dir: str = ""
    config_path: str = ""
    tasks_path: str = ""
    mailbox_dir: str = ""
    permission_mode: str = "default"
    schema_version: int = 1
    revision: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def set_paths(self, directory: str | Path) -> None:
        path = Path(directory)
        self.config_dir = str(path)
        self.config_path = str(path / "config.json")
        self.tasks_path = str(path / "tasks.json")
        self.mailbox_dir = str(path / "mailbox")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "team_id": self.team_id,
            "name": self.name,
            "sanitized_name": self.sanitized_name,
            "lead_agent_id": self.lead_agent_id,
            "backend": self.backend.value,
            "description": self.description,
            "created_at": self.created_at.timestamp(),
            "permission_mode": self.permission_mode,
            "members": [member.to_dict() for member in self.members],
        }

    @classmethod
    def from_dict(cls, value: dict, directory: str | Path) -> Team:
        from novacode.team.repository import stable_team_id_for_path

        created = datetime.fromtimestamp(float(value.get("created_at", 0)), UTC)
        config_path = Path(directory) / "config.json"
        team = cls(
            name=str(value.get("name", "")),
            sanitized_name=str(value.get("sanitized_name", "")),
            lead_agent_id=str(value.get("lead_agent_id", "lead")),
            backend=BackendType(value.get("backend", BackendType.IN_PROCESS)),
            team_id=str(value.get("team_id") or stable_team_id_for_path(config_path)),
            description=str(value.get("description", "")),
            created_at=created,
            members=[TeammateInfo.from_dict(item) for item in value.get("members", [])],
            permission_mode=str(value.get("permission_mode", "default")),
            schema_version=int(value.get("schema_version", 1)),
            revision=int(value.get("revision", 0)),
        )
        team.set_paths(directory)
        return team

    def member_by_name(self, name: str) -> TeammateInfo | None:
        return next((member for member in self.members if member.name == name), None)

    def member_by_agent_id(self, agent_id: str) -> TeammateInfo | None:
        return next((member for member in self.members if member.agent_id == agent_id), None)

    async def add_member(self, info: TeammateInfo) -> None:
        from dataclasses import replace

        from novacode.team.domain import (
            AgentAddress,
            AgentId,
            MemberName,
            TeamId,
            TeamMember,
        )
        from novacode.team.persistence import apply_team_state
        from novacode.team.repository import JsonTeamRepository

        member = TeamMember(
            AgentAddress(TeamId(self.team_id), MemberName(info.name)),
            AgentId(info.agent_id),
            info.is_active,
            agent_type=info.agent_type,
            model=info.model,
            worktree_path=info.worktree_path,
            branch=info.branch,
            backend_type=info.backend_type.value,
            pane_id=info.pane_id,
            plan_mode_required=info.plan_mode_required,
            session_dir=info.session_dir,
        )

        def add(state):
            if any(item.address.member_name == info.name for item in state.members):
                raise MemberExistsError(f"Team 成员已存在: {info.name}")
            return replace(state, members=(*state.members, member))

        state = await JsonTeamRepository(self.config_path).transact(TeamId(self.team_id), add)
        apply_team_state(self, state)

    async def set_member_active(self, name: str, active: bool) -> None:
        from dataclasses import replace

        from novacode.team.domain import MemberName, TeamId
        from novacode.team.persistence import apply_team_state
        from novacode.team.repository import JsonTeamRepository

        def set_active(state):
            if not any(item.address.member_name == name for item in state.members):
                raise MemberNotFoundError(f"Team 成员不存在: {name}")
            members = tuple(
                replace(item, active=active)
                if item.address.member_name == MemberName(name)
                else item
                for item in state.members
            )
            return replace(state, members=members)

        state = await JsonTeamRepository(self.config_path).transact(
            TeamId(self.team_id), set_active
        )
        apply_team_state(self, state)

    async def remove_member(self, name: str) -> None:
        from dataclasses import replace

        from novacode.team.domain import MemberName, TeamId
        from novacode.team.persistence import apply_team_state
        from novacode.team.repository import JsonTeamRepository

        def remove(state):
            if not any(item.address.member_name == name for item in state.members):
                raise MemberNotFoundError(f"Team 成员不存在: {name}")
            members = tuple(
                item for item in state.members if item.address.member_name != MemberName(name)
            )
            return replace(state, members=members)

        state = await JsonTeamRepository(self.config_path).transact(TeamId(self.team_id), remove)
        apply_team_state(self, state)

    async def update_member_identity(
        self,
        name: str,
        *,
        agent_id: str,
        pane_id: str,
    ) -> None:
        from dataclasses import replace

        from novacode.team.domain import AgentId, MemberName, TeamId
        from novacode.team.persistence import apply_team_state
        from novacode.team.repository import JsonTeamRepository

        def update(state):
            if not any(item.address.member_name == name for item in state.members):
                raise MemberNotFoundError(f"Team 成员不存在: {name}")
            members = tuple(
                replace(item, agent_id=AgentId(agent_id), pane_id=pane_id)
                if item.address.member_name == MemberName(name)
                else item
                for item in state.members
            )
            return replace(state, members=members)

        state = await JsonTeamRepository(self.config_path).transact(TeamId(self.team_id), update)
        apply_team_state(self, state)
