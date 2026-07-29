"""斜杠命令补全菜单的纯状态机与渲染。"""

from dataclasses import dataclass, field

from rich.text import Text

from novacode.command import Command, Registry

MAX_ROWS = 8


@dataclass(slots=True)
class CompletionMenu:
    items: list[Command] = field(default_factory=list)
    cursor: int = 0
    offset: int = 0
    active: bool = False

    def update(self, input_text: str, registry: Registry) -> None:
        text = input_text.strip()
        if not text.startswith("/") or "\n" in input_text or "\r" in input_text:
            self.hide()
            return
        self.items = registry.prefix_match(text)
        self.active = True
        self.cursor = min(self.cursor, max(0, len(self.items) - 1))
        self._keep_cursor_visible()

    def move_up(self) -> None:
        if self.items:
            self.cursor = max(0, self.cursor - 1)
            self._keep_cursor_visible()

    def move_down(self) -> None:
        if self.items:
            self.cursor = min(len(self.items) - 1, self.cursor + 1)
            self._keep_cursor_visible()

    def selected(self) -> Command | None:
        return self.items[self.cursor] if self.items else None

    def hide(self) -> None:
        self.items = []
        self.cursor = 0
        self.offset = 0
        self.active = False

    def render(self, width: int) -> Text:
        result = Text(no_wrap=True, overflow="crop")
        if not self.active:
            return result
        if not self.items:
            result.append("  无匹配", style="dim")
            return result

        visible = self.items[self.offset : self.offset + MAX_ROWS]
        name_width = max(len(command.name) for command in self.items)
        for index, command in enumerate(visible, start=self.offset):
            line = f"/{command.name.ljust(name_width)}  {command.description}"
            if width > 0:
                line = line[:width]
            result.append(line, style="reverse" if index == self.cursor else "")
            if index != self.offset + len(visible) - 1:
                result.append("\n")
        return result

    def _keep_cursor_visible(self) -> None:
        if not self.items:
            self.offset = 0
        elif self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + MAX_ROWS:
            self.offset = self.cursor - MAX_ROWS + 1
