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
    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    members: list[TeammateInfo] = field(default_factory=list)
    config_dir: str = ""
    config_path: str = ""
    tasks_path: str = ""
    mailbox_dir: str = ""
    permission_mode: str = "default"
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def set_paths(self, directory: str | Path) -> None:
        path = Path(directory)
        self.config_dir = str(path)
        self.config_path = str(path / "config.json")
        self.tasks_path = str(path / "tasks.json")
        self.mailbox_dir = str(path / "mailbox")

    def to_dict(self) -> dict:
        return {
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
        created = datetime.fromtimestamp(float(value.get("created_at", 0)), UTC)
        team = cls(
            name=str(value.get("name", "")),
            sanitized_name=str(value.get("sanitized_name", "")),
            lead_agent_id=str(value.get("lead_agent_id", "lead")),
            backend=BackendType(value.get("backend", BackendType.IN_PROCESS)),
            description=str(value.get("description", "")),
            created_at=created,
            members=[TeammateInfo.from_dict(item) for item in value.get("members", [])],
            permission_mode=str(value.get("permission_mode", "default")),
        )
        team.set_paths(directory)
        return team

    def member_by_name(self, name: str) -> TeammateInfo | None:
        return next((member for member in self.members if member.name == name), None)

    def member_by_agent_id(self, agent_id: str) -> TeammateInfo | None:
        return next((member for member in self.members if member.agent_id == agent_id), None)

    async def add_member(self, info: TeammateInfo) -> None:
        from novacode.team.persistence import reload_members, save_team

        async with self._lock:
            reload_members(self)
            if self.member_by_name(info.name) is not None:
                raise MemberExistsError(f"Team 成员已存在: {info.name}")
            self.members.append(info)
            save_team(self)

    async def set_member_active(self, name: str, active: bool) -> None:
        from novacode.team.persistence import reload_members, save_team

        async with self._lock:
            reload_members(self)
            member = self.member_by_name(name)
            if member is None:
                raise MemberNotFoundError(f"Team 成员不存在: {name}")
            member.is_active = active
            save_team(self)

    async def remove_member(self, name: str) -> None:
        from novacode.team.persistence import reload_members, save_team

        async with self._lock:
            reload_members(self)
            member = self.member_by_name(name)
            if member is None:
                raise MemberNotFoundError(f"Team 成员不存在: {name}")
            self.members.remove(member)
            save_team(self)
