"""固定本地 CI 记录：同一业务合同覆盖内置和真实 stdio MCP 注册。"""

import json
from typing import Any

from novacode.tool import Result

BUILD = "BUILD-731"
STATUS = {"build_id": BUILD, "status": "FAILED"}
FAILURE = {"build_id": BUILD, "reason": "TEST_TIMEOUT", "retry_allowed": True}


class CiStatusTool:
    read_only = True

    def name(self) -> str:
        return "ci_build_status"

    def description(self) -> str:
        return "查询固定 CI 构建记录的状态，按 build_id 精确查询。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"build_id": {"type": "string"}},
            "required": ["build_id"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        data = json.loads(args)
        if data != {"build_id": BUILD}:
            return Result("未找到构建", is_error=True)
        return Result(json.dumps(STATUS))


def serve() -> None:
    import asyncio

    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    server = Server("fixed-ci")

    @server.list_tools()
    async def listing() -> list[types.Tool]:
        return [
            types.Tool(
                name="ci_build_failure",
                description="查询固定 CI 构建记录的具体失败原因与是否允许重试，按 build_id 查询。",
                inputSchema=CiStatusTool().parameters(),
                annotations=types.ToolAnnotations(readOnlyHint=True),
            )
        ]

    @server.call_tool()
    async def call(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name != "ci_build_failure" or arguments != {"build_id": BUILD}:
            raise ValueError("未找到构建或工具")
        return [types.TextContent(type="text", text=json.dumps(FAILURE))]

    async def main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(main())


if __name__ == "__main__":
    serve()
