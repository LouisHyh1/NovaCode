"""Glob file search tool."""

import json

from novacode.runtime.errors import NovaCodeError
from novacode.search.domain import SearchKind, SearchRequest
from novacode.search.service import FileSearchService
from novacode.tool import Result, resolve_path


class GlobTool:
    read_only = True

    def __init__(self, service: FileSearchService | None = None) -> None:
        self.service = service or FileSearchService()

    def name(self) -> str:
        return "glob"

    def description(self) -> str:
        return (
            "按 glob 模式查找匹配的文件（不包含目录）。"
            "非递归：`*.py` 只匹配根目录下的 .py 文件。"
            "递归：`**/*.py` 匹配所有子目录中的 .py 文件。"
            "返回排序后的相对路径列表，最多 100 条。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
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
        try:
            result = await self.service.search(SearchRequest(SearchKind.GLOB, root, pattern))
        except (NovaCodeError, OSError) as exc:
            return Result(content=f"glob 搜索失败: {exc}", is_error=True)
        metadata = _search_metadata(result)
        if not result.hits:
            hint = "（提示：非递归模式 `*.py` 只查顶层，递归请用 `**/*.py`）"
            content = f"在 {root} 下未匹配到 pattern='{pattern}' 的文件。{hint}"
        else:
            lines = list(result.hits)
            if result.truncated:
                lines.append(f"[truncated: {result.reason}]")
            content = "\n".join(lines)
        return Result(content=content, metadata=metadata)


def _search_metadata(result) -> dict:
    return {
        "truncated": result.truncated,
        "reason": result.reason,
        "scanned_files": result.scanned_files,
        "scanned_bytes": result.scanned_bytes,
        "elapsed": result.elapsed,
    }
