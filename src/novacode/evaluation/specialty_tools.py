"""公开专项工具：固定业务记录、一次故障及真实 stdio MCP；不包含验收答案。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

from novacode.evaluation.contracts import require
from novacode.tool import Registry, Result, Tool, new_default_registry, resolve_path

DESCRIPTIONS = {
    "ci_commit": "按提交 key 查询 CI 失败任务、原因和测试记录。",
    "ci_test": "按测试用例 key 查询 CI 测试失败详情，来源是 ci-tests。",
    "ci_deploy": "按部署用例 key 查询 CI 部署失败详情，来源是 deployment。",
    "issue_lookup": "按用户 issue key 查询关联提交身份。",
}
STANDARD_TEST = "python -m unittest discover -s tests -v"


class RecordTool:
    read_only = True

    def __init__(self, name: str, records: dict[str, Any]) -> None:
        require(name in DESCRIPTIONS, "未知业务工具")
        self._name = name
        self.records = records

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return DESCRIPTIONS[self._name]

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        data = json.loads(args)
        if set(data) != {"key"} or data["key"] not in self.records:
            return Result("未找到记录", is_error=True)
        return Result(json.dumps(self.records[data["key"]], ensure_ascii=False))


class FirstFault:
    def __init__(self, tool: Tool, task_id: str, observer: Any) -> None:
        self.tool, self.task_id, self.observer = tool, task_id, observer
        self.read_only = tool.read_only
        self.triggered = False

    def name(self) -> str:
        return self.tool.name()

    def description(self) -> str:
        return self.tool.description()

    def parameters(self) -> dict[str, Any]:
        return self.tool.parameters()

    def eligible(self, args: str) -> bool:
        try:
            data = json.loads(args)
            if self.task_id == "S05":
                pattern = data.get("pattern")
                if not isinstance(pattern, str) or not pattern:
                    return False
                re.compile(pattern)
                return Path(resolve_path(data.get("path") or ".")).exists()
            return shlex.split(data.get("command", "")) == shlex.split(STANDARD_TEST)
        except (ValueError, TypeError, AttributeError, re.error):
            return False

    async def execute(self, args: str) -> Result:
        if self.triggered or not self.eligible(args):
            return await self.tool.execute(args)
        self.triggered = True
        identity = hashlib.sha256(args.encode()).hexdigest()
        if self.task_id == "S05":
            self.observer(
                "fault_injected",
                rule="first-valid-grep-v1",
                name="grep",
                arguments_sha256=identity,
                cleanup="not-needed",
            )
            return Result(
                "固定首次搜索故障 E_SEARCH_TEMPORARY，可重试或使用合法替代路径。",
                is_error=True,
                metadata={"fault_rule": "first-valid-grep-v1"},
            )
        return await self.timeout_fault(identity)

    async def timeout_fault(self, identity: str) -> Result:
        from novacode.tool.bash import BashTool

        # 用真实进程树和握手模拟指定命令的第一次超时；目标测试后续不变。
        with tempfile.TemporaryDirectory(prefix="specialty-timeout-") as directory:
            marker = Path(directory) / "started"
            child_code = (
                f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(60)"
            )
            parent_code = (
                f"import subprocess,sys; subprocess.run([sys.executable,'-c',{child_code!r}])"
            )
            invocation = json.dumps({"command": shlex.join([sys.executable, "-c", parent_code])})
            task = asyncio.create_task(BashTool().execute(invocation))
            try:
                async with asyncio.timeout(5):
                    while not marker.exists():
                        if task.done():
                            raise RuntimeError("故障进程握手失败")
                        await asyncio.sleep(0.01)
                child_pid = int(marker.read_text())
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                # Linux 中 zombie 已停止执行，不能被当作仍能修改产物的进程。
                status = Path(f"/proc/{child_pid}/stat")
                cleaned = not status.exists() or status.read_text().split()[2] == "Z"
                self.observer(
                    "fault_injected",
                    rule="first-standard-test-timeout-v1",
                    name="bash",
                    arguments_sha256=identity,
                    child_pid=child_pid,
                    cleanup="passed" if cleaned else "failed",
                )
                require(cleaned, "故障子进程仍在运行")
                return Result(
                    "标准测试命令首次执行超时，所属子进程已清理；可重新执行。",
                    is_error=True,
                    metadata={
                        "error_type": "TimeoutError",
                        "fault_rule": "first-standard-test-timeout-v1",
                    },
                )
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


def registry_for(task_id: str, records: dict[str, Any], observer: Any) -> Registry:
    base = new_default_registry()
    registry = Registry()
    for definition in base.definitions():
        tool = base.get(definition.name)
        assert tool is not None
        if (task_id, tool.name()) in (("S05", "grep"), ("S06", "bash")):
            registry.register(FirstFault(tool, task_id, observer))
        else:
            registry.register(tool)
    if task_id == "S10":
        registry.register(RecordTool("ci_commit", records["ci_commit"]))
    if task_id == "S12":
        registry.register(RecordTool("issue_lookup", records["issue_lookup"]))
    return registry


async def connect_records(task_id: str, path: Path) -> Any:
    from novacode import __version__
    from novacode.mcp.config import Config, ServerConfig
    from novacode.mcp.manager import new_manager

    names = {"S11": ["ci_test", "ci_deploy"], "S12": ["ci_commit"]}.get(task_id, [])
    if not names:
        return None
    return await new_manager(
        Config(
            servers={
                "specialty": ServerConfig(
                    type="stdio",
                    command=sys.executable,
                    args=["-m", "novacode.evaluation.specialty_tools", str(path), *names],
                )
            }
        ),
        __version__,
    )


def serve(path: Path, names: list[str]) -> None:
    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    records = json.loads(path.read_text())
    tools = {name: RecordTool(name, records[name]) for name in names}
    server = Server("specialty-records-v1")

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def listing() -> list[types.Tool]:
        return [
            types.Tool(
                name=t.name(),
                description=t.description(),
                inputSchema=t.parameters(),
                annotations=types.ToolAnnotations(readOnlyHint=True),
            )
            for t in tools.values()
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        result = await tools[name].execute(json.dumps(arguments))
        require(not result.is_error, result.content)
        return [types.TextContent(type="text", text=result.content)]

    async def main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(main())


if __name__ == "__main__":
    serve(Path(sys.argv[1]), sys.argv[2:])
