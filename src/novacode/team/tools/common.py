"""Team 工具共用解析与寻址。"""

import json

from novacode.agent.context import current
from novacode.team.types import Team


def parse_args(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def current_teammate_context():
    context = current()
    return getattr(context.agent, "teammate_context", None) if context is not None else None


def resolve_team(manager, data: dict, *, require: bool = True) -> Team | None:
    teammate = current_teammate_context()
    if teammate is not None:
        team = manager.get(teammate.team_name)
        if team is not None:
            return team
    team_name = str(data.get("team_name") or "")
    if team_name:
        team = manager.get(team_name)
        if team is not None:
            return team
    teams = manager.list()
    if len(teams) == 1:
        return teams[0]
    if require:
        raise ValueError("无法确定 Team；请提供 team_name")
    return None
