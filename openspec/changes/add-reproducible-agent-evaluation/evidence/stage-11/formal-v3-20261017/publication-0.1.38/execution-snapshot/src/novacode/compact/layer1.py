"""Layer 1: spill oversized tool results and replace them with stable previews."""

import copy
import re
from pathlib import Path

from novacode.compact.const import (
    MESSAGE_AGGREGATE_LIMIT,
    PREVIEW_HEAD_BYTES,
    PREVIEW_HEAD_LINES,
    SINGLE_RESULT_LIMIT,
)
from novacode.compact.state import ContentReplacementState, SessionContext
from novacode.llm import Message


def _safe_spill_name(tool_use_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_use_id).strip("._")
    return name or "tool-result"


def _bytes(content: str) -> int:
    return len(content.encode("utf-8"))


def _head(content: str) -> str:
    lines = "\n".join(content.splitlines()[:PREVIEW_HEAD_LINES])
    data = lines.encode("utf-8")[:PREVIEW_HEAD_BYTES]
    return data.decode("utf-8", errors="replace")


def spill_single(session: SessionContext, tool_use_id: str, content: str) -> None:
    path = Path(session.spill_dir) / _safe_spill_name(tool_use_id)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_preview(original_bytes: int, head: str, spill_path: str) -> str:
    return (
        "[tool result compacted]\n"
        f"original size: {original_bytes} bytes\n"
        f"[saved to] {spill_path}\n"
        "head preview:\n"
        f"{head}\n\n"
        "完整内容已落盘。如需可靠使用完整内容，请通过文件读取工具重新读取保存路径；"
        "不要凭头部预览猜测被截断的内容。"
    )


def offload_and_snip(
    msgs: list[Message],
    state: ContentReplacementState,
    session: SessionContext,
) -> list[Message]:
    updated = copy.deepcopy(msgs)
    for msg in updated:
        if msg.role != "tool" or not msg.tool_results:
            continue

        original_sizes = [_bytes(result.content) for result in msg.tool_results]
        replace_ids: set[int] = set()

        for idx, size in enumerate(original_sizes):
            if size > SINGLE_RESULT_LIMIT:
                replace_ids.add(idx)

        remaining_total = sum(
            size for idx, size in enumerate(original_sizes) if idx not in replace_ids
        )
        candidates = sorted(
            (idx for idx in range(len(original_sizes)) if idx not in replace_ids),
            key=lambda idx: original_sizes[idx],
            reverse=True,
        )
        for idx in candidates:
            if remaining_total <= MESSAGE_AGGREGATE_LIMIT:
                break
            replace_ids.add(idx)
            remaining_total -= original_sizes[idx]

        for idx, result in enumerate(msg.tool_results):
            original = result.content

            def decide_replace(
                idx: int = idx,
                result_id: str = result.tool_call_id,
            ) -> tuple[str, str]:
                if idx not in replace_ids:
                    return "kept", ""
                try:
                    spill_single(session, result_id, original)
                    spill_path = str(Path(session.spill_dir) / _safe_spill_name(result_id))
                    preview = build_preview(_bytes(original), _head(original), spill_path)
                except OSError:
                    return "skip", ""
                return "replaced", preview

            result.content = state.decide_once(result.tool_call_id, original, decide_replace)
    return updated
