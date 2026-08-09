"""Team JSON 持久化辅助函数。"""

import json
import os
import re
from pathlib import Path
from typing import Any

from novacode.team.domain import (
    AgentAddress,
    AgentId,
    MemberName,
    TeamId,
    TeamMember,
    TeamState,
)
from novacode.team.types import Team, TeammateInfo


def sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-")


def atomic_write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_team(team: Team) -> None:
    atomic_write_json(team.config_path, team.to_dict())


def reload_members(team: Team) -> None:
    """调用方已持有 Team 锁；跨进程修改前从磁盘刷新成员。"""
    try:
        raw = read_json(team.config_path)
        members = raw.get("members")
        if isinstance(members, list):
            team.members = [TeammateInfo.from_dict(item) for item in members]
    except (OSError, TypeError, ValueError):
        return


def team_to_state(team: Team) -> TeamState:
    team_id = TeamId(team.team_id)
    members = tuple(
        TeamMember(
            AgentAddress(team_id, MemberName(member.name)),
            AgentId(member.agent_id),
            member.is_active,
            agent_type=member.agent_type,
            model=member.model,
            worktree_path=member.worktree_path,
            branch=member.branch,
            backend_type=member.backend_type.value,
            pane_id=member.pane_id,
            plan_mode_required=member.plan_mode_required,
            session_dir=member.session_dir,
        )
        for member in team.members
    )
    return TeamState(
        team_id,
        team.name,
        members,
        schema_version=team.schema_version,
        revision=team.revision,
        sanitized_name=team.sanitized_name,
        lead_agent_id=AgentId(team.lead_agent_id),
        backend=team.backend.value,
        description=team.description,
        created_at=team.created_at.timestamp(),
        permission_mode=team.permission_mode,
    )


def apply_team_state(team: Team, state: TeamState) -> None:
    from datetime import UTC, datetime

    from novacode.team.types import BackendType

    team.team_id = str(state.team_id)
    team.name = state.name
    team.sanitized_name = state.sanitized_name
    team.lead_agent_id = str(state.lead_agent_id)
    team.backend = BackendType(state.backend)
    team.description = state.description
    team.created_at = datetime.fromtimestamp(state.created_at, UTC)
    team.permission_mode = state.permission_mode
    team.schema_version = state.schema_version
    team.revision = state.revision
    team.members = [
        TeammateInfo(
            name=str(member.address.member_name),
            agent_id=str(member.agent_id),
            agent_type=member.agent_type,
            model=member.model,
            worktree_path=member.worktree_path,
            branch=member.branch,
            backend_type=BackendType(member.backend_type),
            pane_id=member.pane_id,
            is_active=member.active,
            plan_mode_required=member.plan_mode_required,
            session_dir=member.session_dir,
        )
        for member in state.members
    ]
