import pytest

from novacode.command import Command, Kind, Registry
from novacode.command.ui import NopUI


async def _handler(ui: NopUI) -> None:
    pass


def _command(name: str, *, aliases: list[str] | None = None, hidden: bool = False) -> Command:
    return Command(name, f"{name} description", Kind.LOCAL, _handler, aliases or [], hidden)


def test_register_lookup_and_visible_copy() -> None:
    registry = Registry()
    command = _command("help", aliases=["h"])
    registry.register(command)

    assert registry.lookup("HELP") is command
    assert registry.lookup("h") is command
    visible = registry.visible()
    visible.clear()
    assert registry.visible() == [command]


@pytest.mark.parametrize(
    ("first", "second", "conflict"),
    [
        (_command("help"), _command("help"), "help"),
        (_command("help", aliases=["h"]), _command("history", aliases=["h"]), "h"),
        (_command("help", aliases=["h"]), _command("h"), "h"),
        (_command("first"), _command("help", aliases=["help"]), "help"),
    ],
)
def test_register_conflict_raises(first: Command, second: Command, conflict: str) -> None:
    registry = Registry()
    registry.register(first)

    with pytest.raises(RuntimeError, match=conflict):
        registry.register(second)


def test_visible_sorted_and_hidden_filtered() -> None:
    registry = Registry()
    registry.register(_command("status"))
    registry.register(_command("help"))
    registry.register(_command("secret", hidden=True))

    assert [command.name for command in registry.visible()] == ["help", "status"]
    assert registry.lookup("secret") is not None


def test_prefix_match_uses_only_primary_name() -> None:
    registry = Registry()
    registry.register(_command("status", aliases=["show"]))
    registry.register(_command("session"))

    assert [command.name for command in registry.prefix_match("/s")] == ["session", "status"]
    assert registry.prefix_match("/sh") == []
