"""Team JSON 持久化辅助函数。"""

import json
import os
import re
from pathlib import Path
from typing import Any

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
