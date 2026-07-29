"""TUI 命令相关的共享显示格式。"""

from novacode.agent import CompactPhase


def format_compact_notice(
    phase: CompactPhase,
    before: int = 0,
    after: int = 0,
    err: Exception | None = None,
) -> str:
    if phase == CompactPhase.BEFORE_AUTO:
        return "正在压缩上下文..."
    if phase == CompactPhase.BEFORE_EMERGENCY:
        return "上下文撞墙，自动压缩中..."
    if err is not None:
        return f"压缩失败：{err}"
    if after < before:
        return f"已压缩，token 从 {before} 降至 {after}"
    if after == before:
        return f"已压缩，token 约为 {before}，未明显变化"
    return f"压缩未减少上下文，token 从 {before} 变为 {after}"
