"""TeamDelete 工具。"""

import json

from novacode.team.tools.common import parse_args
from novacode.tool import Result


class TeamDeleteTool:
    read_only = False

    def __init__(self, manager) -> None:
        self.manager = manager

    def name(self) -> str:
        return "TeamDelete"

    def description(self) -> str:
        return "删除 Team；有活跃队员时需 force=true。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string"},
                "force": {"type": "boolean"},
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
            await self.manager.delete(name, data.get("force") is True)
        except Exception as exc:
            return Result(f"Team 删除失败: {exc}", is_error=True)
        return Result(json.dumps({"team_name": name, "status": "deleted"}, ensure_ascii=False))
