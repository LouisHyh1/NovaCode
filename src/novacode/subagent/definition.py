"""SubAgent 角色定义。"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

from novacode.permission import Mode


class Source(IntEnum):
    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3

    def __str__(self) -> str:
        return {
            self.BUILTIN: "builtin",
            self.USER: "user",
            self.PROJECT: "project",
            self.PLUGIN: "plugin",
        }.get(self, "unknown")


@dataclass
class Definition:
    """Markdown + YAML frontmatter 描述的一个子 Agent 角色。"""

    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: Literal["haiku", "sonnet", "opus", "inherit"] = "inherit"
    max_turns: int = 0
    permission_mode: Mode = Mode.DEFAULT
    dont_ask: bool = False
    background: bool = False
    isolation: str = ""
    system_prompt: str = ""
    file_path: str = ""
    source: Source = Source.BUILTIN

    def is_fork(self) -> bool:
        return self.name == "__fork__"
