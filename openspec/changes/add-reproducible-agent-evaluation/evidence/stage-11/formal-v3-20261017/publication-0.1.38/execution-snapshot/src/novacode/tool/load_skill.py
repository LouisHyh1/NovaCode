"""按需激活 Skill 的只读工具。"""

import json

from novacode.tool import Result


class LoadSkill:
    read_only = True
    category = "read"
    is_concurrency_safe = False

    def __init__(self) -> None:
        self._loader = None
        self._agent = None

    def name(self) -> str:
        return "LoadSkill"

    def description(self) -> str:
        return "按名称加载可用 Skill，并把完整 SOP 激活到后续迭代的环境上下文。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }

    def set_loader(self, loader) -> None:
        self._loader = loader

    def set_agent(self, agent) -> None:
        self._agent = agent

    async def execute(self, args: str) -> Result:
        if self._loader is None or self._agent is None:
            return Result("LoadSkill not properly initialized", is_error=True)
        try:
            payload = json.loads(args)
            name = payload["name"]
            if not isinstance(name, str) or not name:
                raise ValueError
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return Result("LoadSkill requires a non-empty string name", is_error=True)
        skill = self._loader.get(name)
        if skill is None:
            available = ", ".join(self._loader.names()) or "none"
            return Result(f"Unknown skill '{name}'. Available: {available}", is_error=True)
        self._agent.activate_skill(skill.name, skill.prompt_body)
        return Result(f"Skill '{skill.name}' activated. SOP pinned to environment context.")
