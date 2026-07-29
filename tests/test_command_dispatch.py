import pytest

from novacode.command import arguments, parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ("", False)),
        ("   ", ("", False)),
        ("hello", ("", False)),
        ("/", ("", True)),
        ("/help", ("help", True)),
        ("  /HELP  ", ("help", True)),
        ("/help xx", ("help", True)),
        ("/help  ", ("help", True)),
        ("//double", ("/double", True)),
        ("/ /help", ("", True)),
    ],
)
def test_parse(text: str, expected: tuple[str, bool]) -> None:
    assert parse(text) == expected


def test_arguments() -> None:
    assert arguments("/skill info test-skill") == "info test-skill"
    assert arguments("/help") == ""
    assert arguments("plain text") == ""
