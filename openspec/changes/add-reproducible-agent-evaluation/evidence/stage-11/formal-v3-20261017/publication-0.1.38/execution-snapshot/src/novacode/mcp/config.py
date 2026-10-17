"""MCP server configuration loading, merging, and validation."""

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


@dataclass
class ServerConfig:
    type: Literal["stdio", "http"]
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    servers: dict[str, ServerConfig] = field(default_factory=dict)


@dataclass
class _RawServer:
    type: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_config(root: str) -> Config:
    try:
        user_path = Path.home() / ".novacode" / "config.yaml"
    except Exception as exc:
        print(f"[mcp] warn: resolve home failed: {exc}", file=sys.stderr)
        user_servers: dict[str, _RawServer] = {}
    else:
        user_servers = _load_file(user_path)

    project_config_servers = _load_file(Path(root) / ".novacode" / "config.yaml", infer_type=True)
    project_servers = _load_file(Path(root) / ".novacode.yaml")

    for layer in (user_servers, project_config_servers, project_servers):
        for name, server in layer.items():
            _apply_expansion(name, server)

    merged = _merge_servers(user_servers, project_config_servers, project_servers)
    valid: dict[str, ServerConfig] = {}
    for name, server in merged.items():
        cfg = _validate_server(name, server)
        if cfg is not None:
            valid[name] = cfg
    return Config(servers=valid)


def _load_file(path: Path, *, infer_type: bool = False) -> dict[str, _RawServer]:
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[mcp] warn: load {path} failed: {exc}", file=sys.stderr)
        return {}

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        print(f"[mcp] warn: load {path} skipped: top-level YAML must be a mapping", file=sys.stderr)
        return {}

    servers = raw.get("mcp_servers") or {}
    if isinstance(servers, dict):
        return _load_servers_mapping(servers, infer_type=infer_type)
    if isinstance(servers, list):
        return _load_servers_list(servers, infer_type=infer_type)

    print(
        f"[mcp] warn: load {path} skipped: mcp_servers must be a mapping or a list",
        file=sys.stderr,
    )
    return {}


def _load_servers_mapping(
    servers: dict[object, object], *, infer_type: bool
) -> dict[str, _RawServer]:
    result: dict[str, _RawServer] = {}
    for name, value in servers.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            print(
                f"[mcp] warn: skip server {name}: server definition must be a mapping",
                file=sys.stderr,
            )
            continue
        result[name] = _raw_server(value, infer_type=infer_type)
    return result


def _load_servers_list(servers: list[object], *, infer_type: bool) -> dict[str, _RawServer]:
    result: dict[str, _RawServer] = {}
    for value in servers:
        if not isinstance(value, dict):
            print(
                "[mcp] warn: skip server entry: server definition must be a mapping",
                file=sys.stderr,
            )
            continue
        name = value.get("name")
        if not isinstance(name, str) or not name:
            print("[mcp] warn: skip server entry: list server requires name", file=sys.stderr)
            continue
        result[name] = _raw_server(value, infer_type=infer_type)
    return result


def _raw_server(value: dict[str, Any], *, infer_type: bool = False) -> _RawServer:
    srv_type = value.get("type") if isinstance(value.get("type"), str) else None
    command = value.get("command") if isinstance(value.get("command"), str) else None
    url = value.get("url") if isinstance(value.get("url"), str) else None
    if infer_type and srv_type is None:
        if command:
            srv_type = "stdio"
        elif url:
            srv_type = "http"

    args = value.get("args", [])
    env = value.get("env", {})
    headers = value.get("headers", {})
    return _RawServer(
        type=srv_type,
        command=command,
        args=[str(item) for item in args] if isinstance(args, list) else [],
        env=_string_map(env),
        url=url,
        headers=_string_map(headers),
    )


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str):
            result[key] = item
    return result


def _expand_vars(value: str) -> tuple[str, list[str]]:
    undefined: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            undefined.append(name)
            return ""
        return os.environ.get(name, "")

    return _VAR_RE.sub(replace, value), undefined


def _apply_expansion(name: str, server: _RawServer) -> None:
    undefined: set[str] = set()
    for mapping in (server.env, server.headers):
        for key, value in list(mapping.items()):
            expanded, missing = _expand_vars(value)
            mapping[key] = expanded
            undefined.update(missing)
    for var_name in sorted(undefined):
        print(
            f"[mcp] warn: undefined env var ${{{var_name}}} referenced by server {name}",
            file=sys.stderr,
        )


def _merge_servers(*layers: dict[str, _RawServer]) -> dict[str, _RawServer]:
    merged: dict[str, _RawServer] = {}
    for layer in layers:
        merged.update(layer)
    return merged


def _validate_server(name: str, server: _RawServer) -> ServerConfig | None:
    if server.type not in ("stdio", "http"):
        return _skip(name, "type must be 'stdio' or 'http'")
    if server.type == "stdio":
        if not server.command:
            return _skip(name, "stdio server requires command")
        return ServerConfig(
            type="stdio",
            command=server.command,
            args=list(server.args),
            env=dict(server.env),
        )
    if not server.url:
        return _skip(name, "http server requires url")
    return ServerConfig(
        type="http",
        url=server.url,
        headers=dict(server.headers),
    )


def _skip(name: str, reason: str) -> None:
    print(f"[mcp] warn: skip server {name}: {reason}", file=sys.stderr)
    return None
