"""远程安装 Skill 的写工具。"""

import json
from collections.abc import Callable
from pathlib import Path

from novacode.skills.install import install_skill
from novacode.tool import Result


class InstallSkillTool:
    read_only = False
    category = "write"

    def __init__(
        self,
        loader,
        install_root: str | Path,
        on_installed: Callable[[], None] | None = None,
    ) -> None:
        self._loader = loader
        self._install_root = Path(install_root)
        self._on_installed = on_installed

    def name(self) -> str:
        return "InstallSkill"

    def description(self) -> str:
        return "从 skills.sh 或 GitHub URL 安装 Skill 到用户目录。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        try:
            payload = json.loads(args)
            url = payload["url"]
            if not isinstance(url, str) or not url:
                raise ValueError
            name = await install_skill(url, self._install_root)
            self._loader.reload()
            if self._on_installed is not None:
                self._on_installed()
            return Result(f"Skill '{name}' installed and loaded.")
        except Exception as exc:
            return Result(f"InstallSkill failed: {exc}", is_error=True)
