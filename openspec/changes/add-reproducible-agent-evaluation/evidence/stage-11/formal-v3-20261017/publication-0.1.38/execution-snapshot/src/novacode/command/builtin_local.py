"""不修改会话或运行状态的本地命令。"""

from novacode.command.command import Handler
from novacode.command.registry import Registry
from novacode.command.ui import UI


def make_help_handler(registry: Registry) -> Handler:
    async def handle_help(ui: UI) -> None:
        commands = registry.visible()
        width = max((len(command.name) for command in commands), default=0)
        ui.println(
            "\n".join(
                f"/{command.name.ljust(width)}  {command.description}" for command in commands
            )
        )

    return handle_help


async def handle_status(ui: UI) -> None:
    fields = [
        ("Mode", str(ui.mode())),
        ("Tokens", f"{ui.usage_in()} in / {ui.usage_out()} out"),
        ("Tools", f"{ui.tool_count()} enabled"),
        ("Memories", f"{len(ui.memory_files())} files"),
        ("Model", ui.model_name()),
        ("Directory", ui.cwd()),
    ]
    width = max(len(key) for key, _ in fields)
    ui.println("\n".join(f"{key + ':':<{width + 1}}  {value}" for key, value in fields))


async def handle_memory(ui: UI) -> None:
    files = ui.memory_files()
    ui.println("\n".join(files) if files else "无已加载的记忆文件")


async def handle_permission(ui: UI) -> None:
    ui.println(str(ui.mode()))


async def handle_session(ui: UI) -> None:
    ui.println(f"Session: {ui.session_id()}\nPath: {ui.session_path()}")
