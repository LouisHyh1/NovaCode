"""CLI MCP 接线测试。"""

from types import SimpleNamespace

import pytest

from novacode import cli
from novacode.tool import Result


class DummyTool:
    read_only = True

    def name(self) -> str:
        return "mcp__demo__echo"

    def description(self) -> str:
        return "Echo"

    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, args: str) -> Result:
        return Result("ok")


class DummyManager:
    def __init__(self) -> None:
        self.closed = False

    def tools(self):
        return [DummyTool()]

    async def close(self) -> None:
        self.closed = True


class EmptyManager(DummyManager):
    def tools(self):
        return []


def test_register_mcp_tools_adds_manager_tools_to_registry():
    from novacode.tool import new_default_registry

    registry = new_default_registry()
    manager = DummyManager()

    cli._register_mcp_tools(registry, manager)

    assert registry.get("mcp__demo__echo") is not None


def test_register_mcp_tools_reports_registered_tool_count(capsys):
    from novacode.tool import new_default_registry

    registry = new_default_registry()

    cli._register_mcp_tools(registry, DummyManager())

    err = capsys.readouterr().err
    assert "registered 1 tool(s)" in err
    assert "mcp__demo__echo" in err


def test_register_mcp_tools_warns_when_configured_servers_produce_no_tools(capsys):
    from novacode.mcp import Config, ServerConfig
    from novacode.tool import new_default_registry

    registry = new_default_registry()
    cfg = Config(servers={"context7": ServerConfig(type="stdio", command="npx")})

    cli._register_mcp_tools(registry, EmptyManager(), cfg)

    err = capsys.readouterr().err
    assert "configured 1 server(s) but registered 0 tool(s)" in err
    assert "context7" in err


def test_main_closes_mcp_manager(monkeypatch: pytest.MonkeyPatch, tmp_path):
    manager = DummyManager()
    app_seen = {}

    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / ".novacode" / "config.yaml"
    config_file.parent.mkdir()
    config_file.write_text(
        "providers:\n  - name: test\n    protocol: openai\n    api_key: x\n    model: test\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.mcp_client, "load_config", lambda root: SimpleNamespace(servers={}))

    async def fake_new_manager(cfg, version):
        return manager

    monkeypatch.setattr(cli.mcp_client, "new_manager", fake_new_manager)

    class FakeApp:
        def __init__(self, providers, registry, version, driver_class=None, engine=None):
            app_seen["registry"] = registry

        async def run_async(self):
            return None

    monkeypatch.setattr(cli, "NovaCodeApp", FakeApp)
    monkeypatch.setattr(cli, "NoAltScreenDriver", object)

    cli.main()

    assert app_seen["registry"].get("mcp__demo__echo") is not None
    assert manager.closed is True


def test_main_missing_config_returns_clean_error(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert "config file not found" in capsys.readouterr().err
