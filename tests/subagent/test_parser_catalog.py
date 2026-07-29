from pathlib import Path

import pytest

from novacode.permission import Mode
from novacode.subagent import Source, builtin_definitions, load_catalog, parse_definition
from novacode.subagent.parser import DefinitionParseError


def _definition(**overrides) -> bytes:
    values = {
        "name": "worker",
        "description": "test worker",
        "extra": "",
        "body": "system body",
    }
    values.update(overrides)
    return (
        "---\n"
        f"name: {values['name']}\n"
        f"description: {values['description']}\n"
        f"{values['extra']}"
        "---\n\n"
        f"{values['body']}"
    ).encode()


def test_definition_fields_and_dont_ask() -> None:
    definition = parse_definition(
        _definition(
            extra=(
                "tools: [read_file]\n"
                "disallowedTools: [bash]\n"
                "model: sonnet\n"
                "maxTurns: 7\n"
                "permissionMode: dontAsk\n"
                "background: true\n"
            )
        ),
        "worker.md",
        Source.PROJECT,
    )
    assert definition.name == "worker"
    assert definition.description == "test worker"
    assert definition.tools == ["read_file"]
    assert definition.disallowed_tools == ["bash"]
    assert definition.model == "sonnet"
    assert definition.max_turns == 7
    assert definition.permission_mode is Mode.DEFAULT
    assert definition.dont_ask is True
    assert definition.background is True
    assert definition.system_prompt == "system body"
    assert definition.file_path == "worker.md"
    assert definition.source is Source.PROJECT


@pytest.mark.parametrize("missing", ["name", "description"])
def test_required_fields(missing: str) -> None:
    kwargs = {missing: ""}
    with pytest.raises(DefinitionParseError):
        parse_definition(_definition(**kwargs), "bad.md", Source.USER)


def test_invalid_model_and_mode_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    definition = parse_definition(
        _definition(extra="model: gpt-4\npermissionMode: weird\n"),
        "bad.md",
        Source.USER,
    )
    assert definition.model == "inherit"
    assert definition.permission_mode is Mode.DEFAULT
    warning = capsys.readouterr().err
    assert "unknown model" in warning
    assert "unknown permissionMode" in warning


def test_builtin_and_project_override(tmp_path: Path) -> None:
    agents = tmp_path / ".novacode" / "agents"
    agents.mkdir(parents=True)
    (agents / "explore.md").write_bytes(_definition(name="Explore", description="project explore"))
    catalog = load_catalog(tmp_path)
    assert {item.name for item in builtin_definitions()} == {
        "Explore",
        "Plan",
        "general-purpose",
    }
    resolved = catalog.resolve("Explore")
    assert resolved is not None
    assert resolved.source is Source.PROJECT
    assert resolved.description == "project explore"
    assert catalog.fork_definition().is_fork()
