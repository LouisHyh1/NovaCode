"""Summary prompt template and parser."""

import logging
import re

from novacode.llm import Message

logger = logging.getLogger(__name__)

SUMMARY_HEADINGS = [
    "## 1 主要请求和意图",
    "## 2 关键技术概念",
    "## 3 文件和代码段",
    "## 4 错误和修复",
    "## 5 问题解决过程",
    "## 6 所有用户消息原文",
    "## 7 待办任务",
    "## 8 当前工作",
    "## 9 可能的下一步",
]


def serialize_conversation(msgs: list[Message]) -> str:
    lines: list[str] = []
    for msg in msgs:
        if msg.role in ("user", "assistant"):
            lines.append(f"{msg.role}: {msg.content}")
            for call in msg.tool_calls:
                lines.append(f"[call {call.name} id={call.id} args={call.input}]")
        elif msg.role == "tool":
            for result in msg.tool_results:
                lines.append(
                    f"[result id={result.tool_call_id} is_error={result.is_error}] {result.content}"
                )
    return "\n".join(lines)


def build_summary_prompt(msgs: list[Message]) -> list[Message]:
    headings = "\n".join(SUMMARY_HEADINGS)
    content = (
        "You are summarizing a coding agent conversation. Output in two phases.\n\n"
        "<analysis>\nWrite private analysis here. It will be discarded.\n</analysis>\n\n"
        "<summary>\n"
        f"{headings}\n"
        "</summary>\n\n"
        "Do not call tools. Output plain text only.\n\n"
        "[conversation]\n"
        f"{serialize_conversation(msgs)}"
    )
    return [Message(role="user", content=content)]


def extract_summary(raw: str) -> str:
    match = re.search(r"<summary>\s*(.*?)\s*</summary>", raw, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    logger.warning("summary tags not found; using raw summary text")
    return raw.strip()
