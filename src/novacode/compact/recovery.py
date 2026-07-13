"""Recovery attachment rendering."""

from novacode.compact.const import RECOVERY_FILE_LIMIT, RECOVERY_TOKENS_PER_FILE
from novacode.compact.state import FileReadRecord
from novacode.compact.token import estimate_tokens
from novacode.llm import Message, ToolDefinition

BOUNDARY_NOTICE = (
    "边界提示：以上内容来自压缩摘要和恢复快照。需要文件原文、错误原文或用户原话时，"
    "请使用文件读取工具重新读取对应路径；不要依据摘要内容做猜测。"
)


def _truncate_file_content(content: str) -> str:
    if estimate_tokens(0, [Message(role="user", content=content)], 0) <= RECOVERY_TOKENS_PER_FILE:
        return content
    max_chars = RECOVERY_TOKENS_PER_FILE * 3
    return content[:max_chars] + "\n(content truncated)"


def render_file_block(rec: FileReadRecord) -> str:
    return (
        f"### {rec.path}\n"
        f"- timestamp: {rec.timestamp.isoformat()}\n"
        "```text\n"
        f"{_truncate_file_content(rec.content)}\n"
        "```"
    )


def render_tools_block(defs: list[ToolDefinition]) -> str:
    if not defs:
        return "(no tools available)"
    lines = []
    for definition in defs:
        lines.append(
            f"- {definition.name}: {definition.description}; schema={definition.input_schema}"
        )
    return "\n".join(lines)


def build_recovery_attachment(
    snapshot: list[FileReadRecord],
    tool_defs: list[ToolDefinition],
) -> str:
    files = snapshot[:RECOVERY_FILE_LIMIT]
    if files:
        file_text = "\n\n".join(render_file_block(rec) for rec in files)
    else:
        file_text = "(no file snapshots)"

    return (
        "## 最近读过的文件\n"
        f"{file_text}\n\n"
        "## 当前可用工具\n"
        f"{render_tools_block(tool_defs)}\n\n"
        "## 上下文边界\n"
        f"{BOUNDARY_NOTICE}"
    )
