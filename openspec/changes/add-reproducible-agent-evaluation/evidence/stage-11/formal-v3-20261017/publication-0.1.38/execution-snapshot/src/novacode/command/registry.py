"""斜杠命令注册中心。"""

from novacode.command.command import Command


class Registry:
    """集中注册命令，并在启动期拒绝名称冲突。"""

    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._visible: list[Command] = []

    def register(self, command: Command, *, replace: bool = False) -> Command | None:
        keys = (command.name, *command.aliases)
        seen: set[str] = set()
        conflicts: list[Command] = []
        for key in keys:
            if not key or key != key.lower():
                raise ValueError(f"command name must be non-empty lowercase: {key!r}")
            if key in self._by_name:
                conflicts.append(self._by_name[key])
            if key in seen or (key in self._by_name and not replace):
                raise RuntimeError(f"command conflict: {key}")
            seen.add(key)
        replaced = conflicts[0] if conflicts else None
        for old in set(id(item) for item in conflicts):
            command_obj = next(item for item in conflicts if id(item) == old)
            self._remove(command_obj)
        for key in keys:
            self._by_name[key] = command
        if not command.hidden:
            self._visible.append(command)
            self._visible.sort(key=lambda item: item.name)
        return replaced

    def unregister(self, name: str) -> Command | None:
        command = self.lookup(name)
        if command is not None:
            self._remove(command)
        return command

    def _remove(self, command: Command) -> None:
        self._by_name = {key: value for key, value in self._by_name.items() if value is not command}
        self._visible = [item for item in self._visible if item is not command]

    def lookup(self, name: str) -> Command | None:
        return self._by_name.get(name.lower())

    def visible(self) -> list[Command]:
        return list(self._visible)

    def prefix_match(self, prefix: str) -> list[Command]:
        normalized = prefix.lstrip("/").lower()
        return [command for command in self._visible if command.name.startswith(normalized)]
