import pytest

from novacode.command import parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ("", False)),
        ("   ", ("", False)),
        ("hello", ("", False)),
        ("/", ("", True)),
        ("/help", ("help", True)),
        ("  /HELP  ", ("help", True)),
        ("/help xx", ("", True)),
        ("/help  ", ("help", True)),
        ("//double", ("/double", True)),
        ("/ /help", ("", True)),
    ],
)
def test_parse(text: str, expected: tuple[str, bool]) -> None:
    assert parse(text) == expected
