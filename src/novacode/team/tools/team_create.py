"""TeamCreate 工具。"""

import json

from novacode.team.tools.common import parse_args
from novacode.tool import Result


class TeamCreateTool:
    read_only = False

    def __init__(self, manager) -> None:
        self.manager = manager

    def name(self) -> str:
        return "TeamCreate"

    def description(self) -> str:
        return "创建长期 Agent Team，并一次性选择 tmux、iTerm2 或 in-process 后端。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string"},
                "description": {"type": "string"},
                "agent_type": {"type": "string"},
            },
            "required": ["team_name"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        data = parse_args(args)
        name = str(data.get("team_name") or "")
        if not name:
            return Result("team_name 为必填项", is_error=True)
        try:
            team = await self.manager.create(name, str(data.get("description") or ""))
        except Exception as exc:
            return Result(f"Team 创建失败: {exc}", is_error=True)
        return Result(
            json.dumps(
                {
                    "team_name": team.sanitized_name,
                    "backend": team.backend.value,
                    "config_path": team.config_path,
                },
                ensure_ascii=False,
            )
        )
