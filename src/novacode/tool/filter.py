"""子 Agent 工具集过滤。"""

from dataclasses import dataclass, field

ALL_AGENT_DISALLOWED_TOOLS = ["Agent"]
CUSTOM_AGENT_DISALLOWED_TOOLS: list[str] = []
ASYNC_AGENT_ALLOWED_TOOLS = [
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "bash",
    "load_skill",
    "install_skill",
    "LoadSkill",
    "InstallSkill",
]


@dataclass
class FilterParams:
    all: list[str]
    source: int
    background: bool
    allowed: list[str] = field(default_factory=list)
    disallowed: list[str] = field(default_factory=list)
    fork: bool = False


def is_mcp_or_skill(name: str) -> bool:
    return name.startswith("mcp__")


def apply_agent_tool_filter(params: FilterParams) -> list[str]:
    blocked = set(ALL_AGENT_DISALLOWED_TOOLS)
    if params.source > 0:
        blocked.update(CUSTOM_AGENT_DISALLOWED_TOOLS)
    result = [
        name for name in params.all if name not in blocked or (params.fork and name == "Agent")
    ]
    if params.background:
        async_allowed = set(ASYNC_AGENT_ALLOWED_TOOLS)
        result = [
            name
            for name in result
            if name in async_allowed or is_mcp_or_skill(name) or (params.fork and name == "Agent")
        ]
    denied = set(params.disallowed)
    result = [name for name in result if name not in denied]
    if params.allowed:
        allowed = set(params.allowed)
        result = [name for name in result if name in allowed]
    return result
