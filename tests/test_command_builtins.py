import pytest

from novacode.command import NopUI, Registry, register_builtins
from novacode.permission import Mode


class RecordingUI(NopUI):
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.errors: list[str] = []
        self.modes: list[Mode] = []
        self.injections: list[tuple[str, str]] = []

    def println(self, message: str) -> None:
        self.messages.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def set_mode(self, mode: Mode) -> None:
        self.modes.append(mode)

    async def inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        self.injections.append((display_label, preset_prompt))


def _registry() -> Registry:
    registry = Registry()
    register_builtins(registry)
    return registry


def test_register_builtins_all_registered() -> None:
    assert [command.name for command in _registry().visible()] == [
        "clear",
        "compact",
        "do",
        "exit",
        "help",
        "hooks",
        "memory",
        "permission",
        "plan",
        "resume",
        "review",
        "session",
        "status",
        "team",
        "worktree",
    ]


@pytest.mark.asyncio
async def test_register_builtins_handlers_run_on_nop_ui() -> None:
    ui = NopUI()
    for command in _registry().visible():
        await command.handler(ui)


@pytest.mark.asyncio
async def test_help_and_status_are_registry_driven() -> None:
    registry = _registry()
    ui = RecordingUI()

    await registry.lookup("help").handler(ui)  # type: ignore[union-attr]
    assert len(ui.messages[0].splitlines()) == 15
    assert all(f"/{command.name}" in ui.messages[0] for command in registry.visible())

    await registry.lookup("status").handler(ui)  # type: ignore[union-attr]
    assert [line.split(":", 1)[0] for line in ui.messages[-1].splitlines()] == [
        "Mode",
        "Tokens",
        "Tools",
        "Memories",
        "Model",
        "Directory",
    ]
    assert "default" in ui.messages[-1]

    await registry.lookup("permission").handler(ui)  # type: ignore[union-attr]
    assert ui.messages[-1] == "default"


@pytest.mark.asyncio
async def test_do_sets_default_mode_and_injects_prompt() -> None:
    command = _registry().lookup("do")
    ui = RecordingUI()

    assert command is not None
    await command.handler(ui)

    assert ui.modes == [Mode.DEFAULT]
    assert ui.injections and ui.injections[0][0] == "/do"
