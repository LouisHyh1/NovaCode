"""CLI MCP 接线测试。"""

from types import SimpleNamespace

import pytest

from novacode import cli
from novacode.config import ConfigError
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
        def __init__(self, providers, registry, version, driver_class=None, engine=None, **kwargs):
            app_seen["registry"] = registry
            app_seen.update(kwargs)

        async def run_async(self):
            return None

    monkeypatch.setattr(cli, "NovaCodeApp", FakeApp)
    monkeypatch.setattr(cli, "NoAltScreenDriver", object)

    cli.main()

    assert app_seen["registry"].get("mcp__demo__echo") is not None
    assert manager.closed is True


@pytest.mark.asyncio
async def test_cli_assembles_session_instructions_memory_and_background_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    manager = DummyManager()
    project = tmp_path / "project"
    project.mkdir()
    config = project / ".novacode" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "providers:\n  - name: test\n    protocol: openai\n    api_key: x\n    model: test\n",
        encoding="utf-8",
    )
    (project / "NOVACODE.md").write_text("project instructions", encoding="utf-8")
    captured = {}

    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "_user_novacode_root", lambda: tmp_path / "home" / ".novacode")
    monkeypatch.setattr(cli.mcp_client, "load_config", lambda root: SimpleNamespace(servers={}))

    async def fake_new_manager(cfg, version):
        return manager

    monkeypatch.setattr(cli.mcp_client, "new_manager", fake_new_manager)

    class FakeApp:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            captured["registry"] = args[1]
            self.cleanup_task = None
            self.governor = None

        async def run_async(self):
            return None

        async def _shutdown_resources(self):
            if self.cleanup_task is not None:
                await self.cleanup_task
            captured["writer"].close()

        def notify_background(self, notice):
            pass

    monkeypatch.setattr(cli, "NovaCodeApp", FakeApp)

    assert await cli._amain() == 0

    assert captured["project_root"] == project.resolve()
    assert captured["session_context"].session_id in captured["session_context"].message_path
    assert captured["writer"].path.parent == project / ".novacode" / "sessions"
    assert captured["extractor"].provider is None
    assert captured["instructions"] == "project instructions"
    assert callable(captured["memory_index"])
    assert captured["registry"].get("manage_memory") is not None
    assert manager.closed is True


@pytest.mark.asyncio
async def test_writer_initialization_failure_returns_nonzero_without_starting_tui(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = project / ".novacode" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "providers:\n  - name: test\n    protocol: openai\n    api_key: x\n    model: test\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "_user_novacode_root", lambda: tmp_path / "home" / ".novacode")
    started = False

    class BrokenWriter:
        def __init__(self, *args, **kwargs):
            raise OSError("read-only disk")

    class ForbiddenApp:
        def __init__(self, *args, **kwargs):
            nonlocal started
            started = True

    monkeypatch.setattr(cli, "SessionWriter", BrokenWriter)
    monkeypatch.setattr(cli, "NovaCodeApp", ForbiddenApp)

    assert await cli._amain() == 1
    assert started is False
    assert "session writer" in capsys.readouterr().err.lower()


def test_main_missing_config_returns_clean_error(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert "config file not found" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_invalid_project_provider_config_does_not_fallback_to_user(
    tmp_path, monkeypatch, capsys
):
    project = tmp_path / "project"
    project.mkdir()
    project_config = project / ".novacode" / "config.yaml"
    project_config.parent.mkdir()
    project_config.write_text("providers: invalid", encoding="utf-8")
    calls = []

    def fake_load(path):
        calls.append(path)
        if len(calls) > 1:
            raise AssertionError("invalid project config must not fall back")
        raise ConfigError("invalid project config")

    monkeypatch.setattr(cli.os, "getcwd", lambda: str(project))
    monkeypatch.setattr("novacode.config.load", fake_load)

    code = await cli._amain()

    assert code == 1
    assert calls == [str(project_config)]
    assert "invalid project config" in capsys.readouterr().err
