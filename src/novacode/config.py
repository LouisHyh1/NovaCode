"""Configuration data types and YAML loader — supports ${VAR} env-var expansion."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


class ConfigError(Exception):
    pass


@dataclass
class ProviderConfig:
    name: str
    protocol: Literal["anthropic", "openai"]
    api_key: str
    model: str
    base_url: str | None = None
    thinking: bool = False
    context_window: int = 0


@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)
    enable_subagent_background: bool | None = None

    def effective_enable_subagent_background(self) -> bool:
        return self.enable_subagent_background is not False


def load(path: str) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {path}")

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML parse error in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError("Config top-level must be a mapping")
    if "providers" not in raw:
        raise ConfigError("Config must contain a 'providers' key with at least one entry")

    providers_raw = raw["providers"]
    if not isinstance(providers_raw, list) or len(providers_raw) == 0:
        raise ConfigError("'providers' must be a non-empty list")

    providers: list[ProviderConfig] = []
    for i, entry in enumerate(providers_raw):
        prefix = f"providers[{i}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{prefix}: must be a mapping")
        _validate_provider(entry, prefix)
        providers.append(
            ProviderConfig(
                name=entry["name"],
                protocol=entry["protocol"],
                api_key=os.path.expandvars(entry["api_key"]),
                model=entry["model"],
                base_url=os.path.expandvars(entry.get("base_url") or "") or None,
                thinking=entry.get("thinking", False),
                context_window=entry.get("context_window", 0),
            )
        )

    background = raw.get(
        "enable_subagent_background",
        raw.get("enableSubAgentBackground"),
    )
    if background is not None and type(background) is not bool:
        raise ConfigError("enable_subagent_background must be a boolean")
    return Config(providers=providers, enable_subagent_background=background)


def _validate_provider(entry: dict, prefix: str) -> None:
    for fld in ("name", "protocol", "api_key", "model"):
        value = entry.get(fld)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{prefix}.{fld} must be a non-empty string")
    if entry["protocol"] not in ("anthropic", "openai"):
        raise ConfigError(
            f"{prefix}.protocol must be 'anthropic' or 'openai', got '{entry['protocol']}'"
        )
    if "thinking" in entry and not isinstance(entry["thinking"], bool):
        raise ConfigError(f"{prefix}.thinking must be a boolean")
    if "base_url" in entry and entry["base_url"] is not None:
        if not isinstance(entry["base_url"], str):
            raise ConfigError(f"{prefix}.base_url must be a string or null")
    if "context_window" in entry:
        value = entry["context_window"]
        if type(value) is not int:
            raise ConfigError(f"{prefix}.context_window must be an integer")
        if value <= 33_000:
            raise ConfigError(f"{prefix}.context_window must be greater than 33000")


DEFAULT_ANTHROPIC_CONTEXT_WINDOW = 200_000
DEFAULT_OPENAI_CONTEXT_WINDOW = 128_000


def effective_context_window(p: ProviderConfig) -> int:
    if p.context_window > 0:
        return p.context_window
    if p.protocol == "openai":
        return DEFAULT_OPENAI_CONTEXT_WINDOW
    return DEFAULT_ANTHROPIC_CONTEXT_WINDOW
