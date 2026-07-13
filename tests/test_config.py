"""Tests for config module."""

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


def test_context_window_loaded_from_yaml(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("""
providers:
  - name: test
    protocol: openai
    api_key: sk-test
    model: gpt-test
    context_window: 12345
""")

    cfg = load(str(p))

    assert cfg.providers[0].context_window == 12345


def test_effective_context_window_defaults_and_override() -> None:
    assert effective_context_window(ProviderConfig("claude", "anthropic", "k", "m")) == 200000
    assert effective_context_window(ProviderConfig("gpt", "openai", "k", "m")) == 128000
    assert (
        effective_context_window(ProviderConfig("small", "openai", "k", "m", context_window=777))
        == 777
    )
