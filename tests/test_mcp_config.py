"""MCP 配置加载测试。"""

from pathlib import Path

import pytest
import yaml

from novacode.mcp.config import Config, ServerConfig, load_config


def _write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_missing_layers_return_empty_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    cfg = load_config(str(tmp_path / "project"))

    assert cfg == Config()


def test_project_server_overrides_user_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    root = tmp_path / "project"
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_yaml(
        home / ".novacode" / "config.yaml",
        {
            "mcp_servers": {
                "shared": {"type": "stdio", "command": "user-cmd", "args": ["--user"]},
                "user_only": {"type": "http", "url": "https://user.example/mcp"},
            }
        },
    )
    _write_yaml(
        root / ".novacode.yaml",
        {
            "mcp_servers": {
                "shared": {"type": "stdio", "command": "project-cmd"},
                "project_only": {"type": "http", "url": "https://project.example/mcp"},
            }
        },
    )

    cfg = load_config(str(root))

    assert cfg.servers["shared"] == ServerConfig(type="stdio", command="project-cmd")
    assert cfg.servers["user_only"].url == "https://user.example/mcp"
    assert cfg.servers["project_only"].url == "https://project.example/mcp"


def test_project_dot_novacode_config_mcp_servers_are_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    root = tmp_path / "project"
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_yaml(
        root / ".novacode" / "config.yaml",
        {
            "providers": [
                {
                    "name": "test",
                    "protocol": "openai",
                    "api_key": "x",
                    "model": "test",
                }
            ],
            "mcp_servers": [
                {
                    "name": "context7",
                    "command": "npx",
                    "args": ["-y", "@upstash/context7-mcp"],
                }
            ],
        },
    )

    cfg = load_config(str(root))

    assert cfg.servers["context7"] == ServerConfig(
        type="stdio",
        command="npx",
        args=["-y", "@upstash/context7-mcp"],
    )


def test_root_project_mcp_yaml_overrides_dot_novacode_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    root = tmp_path / "project"
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_yaml(
        root / ".novacode" / "config.yaml",
        {"mcp_servers": {"context7": {"type": "stdio", "command": "from-config"}}},
    )
    _write_yaml(
        root / ".novacode.yaml",
        {"mcp_servers": {"context7": {"type": "stdio", "command": "from-root-yaml"}}},
    )

    cfg = load_config(str(root))

    assert cfg.servers["context7"].command == "from-root-yaml"


def test_invalid_yaml_layer_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    home = tmp_path / "home"
    root = tmp_path / "project"
    monkeypatch.setattr(Path, "home", lambda: home)
    user_file = home / ".novacode" / "config.yaml"
    user_file.parent.mkdir(parents=True)
    user_file.write_text("{ bad yaml !!! [[[", encoding="utf-8")
    _write_yaml(
        root / ".novacode.yaml",
        {"mcp_servers": {"ok": {"type": "stdio", "command": "python"}}},
    )

    cfg = load_config(str(root))

    assert list(cfg.servers) == ["ok"]
    assert "[mcp] warn: load" in capsys.readouterr().err


def test_expands_only_env_and_headers_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    home = tmp_path / "home"
    root = tmp_path / "project"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("TOKEN", "secret-value")
    monkeypatch.delenv("MISSING", raising=False)
    _write_yaml(
        root / ".novacode.yaml",
        {
            "mcp_servers": {
                "demo": {
                    "type": "stdio",
                    "command": "${TOKEN}",
                    "args": ["${TOKEN}"],
                    "env": {"TOKEN": "${TOKEN}", "EMPTY": "${MISSING}"},
                },
                "remote": {
                    "type": "http",
                    "url": "https://example.test/mcp",
                    "headers": {"Authorization": "Bearer ${TOKEN}"},
                },
            }
        },
    )

    cfg = load_config(str(root))

    assert cfg.servers["demo"].command == "${TOKEN}"
    assert cfg.servers["demo"].args == ["${TOKEN}"]
    assert cfg.servers["demo"].env == {"TOKEN": "secret-value", "EMPTY": ""}
    assert cfg.servers["remote"].headers["Authorization"] == "Bearer secret-value"
    assert "undefined env var ${MISSING}" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("missing_type", {"command": "python"}),
        ("bad_type", {"type": "sse", "url": "x"}),
        ("stdio_missing_command", {"type": "stdio"}),
        ("http_missing_url", {"type": "http"}),
    ],
)
def test_invalid_server_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    name: str,
    server: dict[str, object],
):
    root = tmp_path / "project"
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    _write_yaml(
        root / ".novacode.yaml",
        {"mcp_servers": {name: server, "ok": {"type": "stdio", "command": "python"}}},
    )

    cfg = load_config(str(root))

    assert list(cfg.servers) == ["ok"]
    assert f"skip server {name}" in capsys.readouterr().err
