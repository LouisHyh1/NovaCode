from unittest.mock import AsyncMock

import pytest

from novacode.command import Registry, register_builtins
from novacode.tui.app import ChatInput
from novacode.tui.complete import MAX_ROWS, CompletionMenu
from tests.test_tui import _make_app


def _menu() -> tuple[CompletionMenu, Registry]:
    registry = Registry()
    register_builtins(registry)
    return CompletionMenu(), registry


def test_completion_activates_filters_and_hides() -> None:
    menu, registry = _menu()

    menu.update("/", registry)
    assert menu.active is True
    assert len(menu.items) == 15

    menu.update("/s", registry)
    assert [command.name for command in menu.items] == ["session", "status"]

    menu.update("hello", registry)
    assert menu.active is False


def test_completion_zero_match_and_multiline() -> None:
    menu, registry = _menu()

    menu.update("/missing", registry)
    assert menu.active is True
    assert menu.selected() is None
    assert "无匹配" in menu.render(80).plain

    menu.update("/s\nnext", registry)
    assert menu.active is False

    menu.update("/skill info test-skill", registry)
    assert menu.active is False


def test_completion_cursor_stays_in_eight_row_window() -> None:
    menu, registry = _menu()
    menu.update("/", registry)

    for _ in range(MAX_ROWS + 1):
        menu.move_down()

    assert menu.cursor == MAX_ROWS + 1
    assert menu.offset == 2
    assert len(menu.render(80).plain.splitlines()) == MAX_ROWS


@pytest.mark.asyncio
async def test_completion_keyboard_integration(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("novacode.skills.loader.USER_SKILLS_DIR", str(tmp_path / "user-skills"))
    app = _make_app()
    async with app.run_test(size=(100, 40)) as pilot:
        app.dispatch_slash = AsyncMock(return_value=True)

        await pilot.press("/")
        assert app.completion.active is True
        assert len(app.completion.items) == 16
        assert any(command.name == "skill" for command in app.completion.items)

        await pilot.press("s")
        assert [command.name for command in app.completion.items] == [
            "session",
            "skill",
            "status",
        ]

        await pilot.press("down", "enter")
        await pilot.pause()
        app.dispatch_slash.assert_awaited_once_with("/skill")
        app.dispatch_slash.reset_mock()

        await pilot.press("/", "s")
        await pilot.press("escape")
        assert app.completion.active is False
        chat_input = app.query_one("#chat-input", ChatInput)
        assert chat_input.text == "/s"
        assert app.focused is chat_input

        chat_input.text = "/s"
        await pilot.pause()
        assert app.completion.active is True
        await pilot.press("tab")
        await pilot.pause()
        assert app.completion.active is False
        app.dispatch_slash.assert_awaited_once_with("/session")
        assert app.query_one("#chat-input", ChatInput).text == ""
