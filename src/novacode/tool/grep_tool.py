"""Grep content search tool."""

import json

from novacode.runtime.errors import NovaCodeError
from novacode.search.domain import SearchKind, SearchRequest
from novacode.search.service import FileSearchService
from novacode.tool import Result, resolve_path
from novacode.tool.glob_tool import _search_metadata


class GrepTool:
    read_only = True

    def __init__(self, service: FileSearchService | None = None) -> None:
        self.service = service or FileSearchService()

    def name(self) -> str:
        return "grep"

    def description(self) -> str:
        return (
            "在文件内容中按正则表达式搜索，返回匹配位置（文件名:行号:内容）。最多返回 100 条命中。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
            },
            "required": ["pattern"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数 JSON 解析失败: {exc}", is_error=True)
        pattern = data.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return Result(content="缺少必填参数: pattern", is_error=True)
        root = resolve_path(data.get("path") or ".")
        file_glob = data.get("glob")
        if file_glob is not None and not isinstance(file_glob, str):
            return Result(content="glob 必须是字符串", is_error=True)
        try:
            result = await self.service.search(
                SearchRequest(
                    SearchKind.GREP,
                    root,
                    pattern,
                    file_glob=file_glob or "",
                )
            )
        except (NovaCodeError, OSError) as exc:
            message = str(exc)
            prefix = "" if message.startswith("正则非法") else "grep 搜索失败: "
            return Result(content=f"{prefix}{message}", is_error=True)
        lines = list(result.hits)
        if result.truncated:
            lines.append(f"[truncated: {result.reason}]")
        content = "\n".join(lines) if lines else f"在 {root} 下搜索 pattern='{pattern}' 无命中"
        return Result(content=content, metadata=_search_metadata(result))
