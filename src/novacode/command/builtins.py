"""13 条内置斜杠命令的集中注册入口。"""

from novacode.command.builtin_hooks import handle_hooks
from novacode.command.builtin_local import (
    handle_memory,
    handle_permission,
    handle_session,
    handle_status,
    make_help_handler,
)
from novacode.command.builtin_prompt import handle_do, handle_review
from novacode.command.builtin_team import handle_team
from novacode.command.builtin_ui import (
    handle_clear,
    handle_compact,
    handle_exit,
    handle_plan,
    handle_resume,
)
from novacode.command.builtin_worktree import handle_worktree
from novacode.command.command import Command, Kind
from novacode.command.registry import Registry


def register_builtins(registry: Registry) -> None:
    commands = [
        Command("clear", "清空当前会话并开启新会话", Kind.UI, handle_clear),
        Command("compact", "立即压缩当前上下文", Kind.UI, handle_compact),
        Command("do", "执行上方已经确认的计划", Kind.PROMPT, handle_do),
        Command("exit", "退出 NovaCode", Kind.UI, handle_exit),
        Command("help", "显示可用命令", Kind.LOCAL, make_help_handler(registry)),
        Command("hooks", "列出已加载的生命周期 Hook", Kind.LOCAL, handle_hooks),
        Command("memory", "显示已加载的记忆文件", Kind.LOCAL, handle_memory),
        Command("permission", "显示当前权限模式", Kind.LOCAL, handle_permission),
        Command("plan", "切换到计划模式", Kind.UI, handle_plan),
        Command("resume", "恢复历史会话", Kind.UI, handle_resume),
        Command("review", "请求 AI 审查当前代码上下文", Kind.PROMPT, handle_review),
        Command("session", "显示当前会话信息", Kind.LOCAL, handle_session),
        Command("status", "显示 NovaCode 运行状态", Kind.LOCAL, handle_status),
        Command("team", "管理 Agent Team", Kind.LOCAL, handle_team),
        Command("worktree", "管理隔离的 Git Worktree", Kind.UI, handle_worktree),
    ]
    for command in commands:
        registry.register(command)
