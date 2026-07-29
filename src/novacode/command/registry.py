"""斜杠命令注册中心。"""

from novacode.command.command import Command


class Registry:
    """集中注册命令，并在启动期拒绝名称冲突。"""

    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._visible: list[Command] = []

    def register(self, command: Command) -> None:
        keys = (command.name, *command.aliases)
        seen: set[str] = set()
        for key in keys:
            if not key or key != key.lower():
                raise ValueError(f"command name must be non-empty lowercase: {key!r}")
            if key in seen or key in self._by_name:
                raise RuntimeError(f"command conflict: {key}")
            seen.add(key)
        for key in keys:
            self._by_name[key] = command
        if not command.hidden:
            self._visible.append(command)
            self._visible.sort(key=lambda item: item.name)

    def lookup(self, name: str) -> Command | None:
        return self._by_name.get(name.lower())

    def visible(self) -> list[Command]:
        return list(self._visible)

    def prefix_match(self, prefix: str) -> list[Command]:
        normalized = prefix.lstrip("/").lower()
        return [command for command in self._visible if command.name.startswith(normalized)]
