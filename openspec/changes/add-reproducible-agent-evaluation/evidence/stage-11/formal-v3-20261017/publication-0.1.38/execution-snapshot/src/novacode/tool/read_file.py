"""Read file tool."""

import json

from novacode.runtime.errors import NovaCodeError
from novacode.search.domain import ReadRequest
from novacode.search.service import FileSearchService
from novacode.tool import Result, resolve_path


class ReadFileTool:
    read_only = True

    def __init__(self, service: FileSearchService | None = None) -> None:
        self.service = service or FileSearchService()

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "读取指定路径的文件内容，返回带行号的文本。文件不存在或不可读时返回结构化错误。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数 JSON 解析失败: {exc}", is_error=True)
        path = data.get("path")
        if not isinstance(path, str) or not path:
            return Result(content="缺少必填参数: path", is_error=True)
        try:
            result = await self.service.read(ReadRequest(resolve_path(path)))
        except (NovaCodeError, OSError) as exc:
            message = str(exc)
            if "不存在" in message or "不是文件" in message:
                return Result(content=f"文件不存在: {path}", is_error=True)
            return Result(content=f"读取文件失败: {message}", is_error=True)
        numbered = "\n".join(
            f"{index:6d}\t{line}" for index, line in enumerate(result.content.splitlines(), 1)
        )
        if result.truncated:
            numbered += f"\n[truncated: {result.reason}]"
        return Result(
            content=numbered,
            metadata={
                "truncated": result.truncated,
                "reason": result.reason,
                "lines": result.lines,
                "bytes_read": result.bytes_read,
            },
        )
