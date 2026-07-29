"""修改 TUI 状态的内置命令。"""

from novacode.command.ui import UI
from novacode.permission import Mode


async def handle_exit(ui: UI) -> None:
    ui.quit()


async def handle_plan(ui: UI) -> None:
    ui.set_mode(Mode.PLAN)
    ui.println("已切换到 PLAN 模式")


async def handle_compact(ui: UI) -> None:
    await ui.force_compact()


async def handle_resume(ui: UI) -> None:
    await ui.open_resume_menu()


async def handle_clear(ui: UI) -> None:
    await ui.clear_and_new_session()
