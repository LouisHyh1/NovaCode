"""Tests for config module."""

import tomllib
from pathlib import Path

import pytest

from novacode.config import ConfigError, ProviderConfig, effective_context_window, load


def test_single_provider(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("""
providers:
  - name: test
    protocol: anthropic
    api_key: sk-test
    model: claude-sonnet-4-6
    thinking: true
""")
    cfg = load(str(p))
    assert len(cfg.providers) == 1
    assert cfg.providers[0].name == "test"
    assert cfg.providers[0].thinking is True
    assert cfg.providers[0].context_window == 0


def test_missing_api_key(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("""
providers:
  - name: test
    protocol: anthropic
    model: claude-sonnet-4-6
""")
    with pytest.raises(ConfigError, match="api_key"):
        load(str(p))


def test_invalid_protocol(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("""
providers:
  - name: test
    protocol: gemini
    api_key: k
    model: m
""")
    with pytest.raises(ConfigError, match="protocol"):
        load(str(p))


def test_file_not_found(tmp_path: Path) -> None:
    p = tmp_path / "nonexistent.yaml"
    with pytest.raises(ConfigError, match="not found"):
        load(str(p))


def test_empty_providers(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("providers: []")
    with pytest.raises(ConfigError, match="non-empty"):
        load(str(p))


@pytest.mark.parametrize("content", ["123", "- item"])
def test_top_level_must_be_mapping(tmp_path: Path, content: str) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match="mapping"):
        load(str(p))


@pytest.mark.parametrize("field", ["name", "protocol", "api_key", "model"])
def test_required_provider_fields_must_be_non_empty_strings(tmp_path: Path, field: str) -> None:
    values = {"name": "test", "protocol": "openai", "api_key": "key", "model": "model"}
    values[field] = ""
    p = tmp_path / "config.yaml"
    p.write_text(
        "providers:\n"
        f"  - name: {values['name']!r}\n"
        f"    protocol: {values['protocol']!r}\n"
        f"    api_key: {values['api_key']!r}\n"
        f"    model: {values['model']!r}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=field):
        load(str(p))


def test_numeric_api_key_is_config_error(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        "providers:\n  - name: test\n    protocol: openai\n    api_key: 123\n    model: m\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="api_key"):
        load(str(p))


@pytest.mark.parametrize(
    ("field", "value"),
    [("thinking", "yes"), ("base_url", 123), ("context_window", True)],
)
def test_optional_provider_fields_validate_actual_types(tmp_path: Path, field: str, value) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        "providers:\n"
        "  - name: test\n"
        "    protocol: openai\n"
        "    api_key: key\n"
        "    model: m\n"
        f"    {field}: {value!r}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=field):
        load(str(p))


@pytest.mark.parametrize("context_window", [0, 33_000])
def test_explicit_context_window_must_exceed_33000(tmp_path: Path, context_window: int) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        "providers:\n"
        "  - name: test\n"
        "    protocol: openai\n"
        "    api_key: key\n"
        "    model: m\n"
        f"    context_window: {context_window}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="greater than 33000"):
        load(str(p))


def test_context_window_loaded_from_yaml(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("""
providers:
  - name: test
    protocol: openai
    api_key: sk-test
    model: gpt-test
    context_window: 80000
""")

    cfg = load(str(p))

    assert cfg.providers[0].context_window == 80000


def test_effective_context_window_defaults_and_override() -> None:
    assert effective_context_window(ProviderConfig("claude", "anthropic", "k", "m")) == 200000
    assert effective_context_window(ProviderConfig("gpt", "openai", "k", "m")) == 128000
    assert (
        effective_context_window(
            ProviderConfig("custom", "openai", "k", "m", context_window=100000)
        )
        == 100000
    )


def test_enable_subagent_background(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "providers:\n"
        "  - name: test\n"
        "    protocol: openai\n"
        "    api_key: key\n"
        "    model: model\n"
        "enable_subagent_background: false\n",
        encoding="utf-8",
    )
    config = load(str(path))
    assert config.enable_subagent_background is False
    assert config.effective_enable_subagent_background() is False


def test_team_features_loaded(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "providers:\n"
        "  - name: test\n"
        "    protocol: openai\n"
        "    api_key: key\n"
        "    model: model\n"
        "features:\n"
        "  coordinator_mode: true\n"
        "  fork_teammate: true\n",
        encoding="utf-8",
    )
    config = load(str(path))
    assert config.features.coordinator_mode is True
    assert config.features.fork_teammate is True


def test_pyproject_version_matches_runtime_version() -> None:
    from novacode import __version__

    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    project_version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    assert project_version == __version__
