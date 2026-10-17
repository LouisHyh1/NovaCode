"""把远端 MCP 工具适配为 NovaCode Tool。"""

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Protocol

import mcp.types as mtypes

from novacode.tool import Result

call_timeout: float = 30.0

_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_non_text_warn_once: set[str] = set()


class CallerSession(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> Any: ...


@dataclass
class McpTool:
    """MCP 远端工具的 NovaCode Tool 包装。"""

    full_name: str
    remote_name: str
    _description: str
    _parameters: dict[str, Any]
    read_only: bool
    caller: CallerSession

    def name(self) -> str:
        return self.full_name

    def description(self) -> str:
        return self._description

    def parameters(self) -> dict[str, Any]:
        return dict(self._parameters)

    async def execute(self, args: str) -> Result:
        try:
            parsed = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数不是合法 JSON: {exc}", is_error=True)
        if not isinstance(parsed, dict):
            return Result(content="参数必须是 JSON object", is_error=True)

        try:
            result = await asyncio.wait_for(
                self.caller.call_tool(self.remote_name, parsed or None),
                timeout=call_timeout,
            )
        except TimeoutError:
            return Result(content=f"MCP 工具调用超时 ({call_timeout:g}s)", is_error=True)
        except Exception as exc:
            return Result(content=f"MCP 工具调用失败: {exc}", is_error=True)

        texts: list[str] = []
        dropped = False
        for block in getattr(result, "content", []) or []:
            if _is_text_content(block):
                texts.append(getattr(block, "text", ""))
            else:
                dropped = True

        if dropped and self.full_name not in _non_text_warn_once:
            _non_text_warn_once.add(self.full_name)
            print(
                f"[mcp] warn: tool {self.full_name} returned non-text content blocks (dropped)",
                file=sys.stderr,
            )

        return Result(content="\n".join(texts), is_error=bool(getattr(result, "isError", False)))


def adapt_tool(server_name: str, remote_tool: Any, session: CallerSession) -> McpTool | None:
    """把 SDK Tool 对象转成 NovaCode Tool；非法名称直接跳过。"""

    remote_name = str(getattr(remote_tool, "name", ""))
    full_name = f"mcp__{server_name}__{remote_name}"
    if not _VALID_NAME.fullmatch(full_name):
        print(
            f"[mcp] warn: skip tool {full_name}: name contains illegal characters",
            file=sys.stderr,
        )
        return None

    description = getattr(remote_tool, "description", "") or (
        f"来自 MCP server {server_name} 的工具 {remote_name}"
    )
    raw_schema = (
        getattr(remote_tool, "inputSchema", None)
        or getattr(remote_tool, "input_schema", None)
        or getattr(remote_tool, "input_schema_", None)
    )
    parameters = (
        dict(raw_schema) if isinstance(raw_schema, dict) and raw_schema else {"type": "object"}
    )
    annotations = getattr(remote_tool, "annotations", None)
    read_only = bool(getattr(annotations, "readOnlyHint", False))
    return McpTool(
        full_name=full_name,
        remote_name=remote_name,
        _description=description,
        _parameters=parameters,
        read_only=read_only,
        caller=session,
    )


def _is_text_content(block: Any) -> bool:
    return isinstance(block, mtypes.TextContent)
