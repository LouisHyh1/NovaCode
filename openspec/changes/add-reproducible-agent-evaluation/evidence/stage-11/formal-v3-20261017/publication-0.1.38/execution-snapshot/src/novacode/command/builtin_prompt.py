"""向会话注入固定提示词的内置命令。"""

from novacode import prompt
from novacode.command.ui import UI
from novacode.permission import Mode

REVIEW_DIRECTIVE = (
    "请审查当前上下文中的代码变更和已读取的文件，指出潜在 bug、可读性问题和可简化处。"
)


async def handle_do(ui: UI) -> None:
    ui.set_mode(Mode.DEFAULT)
    await ui.inject_and_send("/do", prompt.EXECUTE_DIRECTIVE)


async def handle_review(ui: UI) -> None:
    await ui.inject_and_send("/review", REVIEW_DIRECTIVE)
