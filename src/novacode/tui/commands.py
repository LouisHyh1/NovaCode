"""TUI slash command dispatch."""

from collections.abc import Awaitable, Callable
from typing import Any

from novacode.agent import CompactPhase
from novacode.permission import Mode
from novacode.prompt import EXECUTE_DIRECTIVE

CommandHandler = Callable[[Any], Awaitable[None]]


async def handle_exit(app: Any) -> None:
    app.exit()


async def handle_plan(app: Any) -> None:
    app.mode = Mode.PLAN
    app._update_mode_label()
    app._show_system("已进入计划模式（只读工具）。输入需求后会先调研并给出计划。")


async def handle_do(app: Any) -> None:
    app.mode = Mode.DEFAULT
    app._update_mode_label()
    app.conv.add_user(EXECUTE_DIRECTIVE)
    await app._start_stream()


async def handle_compact(app: Any) -> None:
    if getattr(app, "agent", None) is None:
        app._show_system("压缩失败：当前没有可用 Agent")
        return
    try:
        before, after = await app.agent.run_force_compact(app.conv, app._current_tool_defs())
    except Exception as exc:
        app._show_system(format_compact_notice(CompactPhase.AFTER_AUTO, 0, 0, exc))
        return
    app._show_system(format_compact_notice(CompactPhase.AFTER_AUTO, before, after, None))


async def handle_unknown(app: Any) -> None:
    app._show_system("未知命令")


BUILTIN_COMMANDS: dict[str, CommandHandler] = {
    "/exit": handle_exit,
    "/plan": handle_plan,
    "/do": handle_do,
    "/compact": handle_compact,
}


def dispatch_command(input_: str) -> tuple[CommandHandler | None, bool]:
    if not input_.startswith("/"):
        return None, False
    return BUILTIN_COMMANDS.get(input_, handle_unknown), True


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
