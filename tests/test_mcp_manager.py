"""MCP Manager 生命周期测试。"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from novacode.mcp import manager as manager_mod
from novacode.mcp.config import Config, ServerConfig
from novacode.mcp.manager import Manager, new_manager
from novacode.mcp.tool import McpTool


async def _fake_connect(
    mgr: Manager,
    name: str,
    srv: ServerConfig,
    version: str,
    stack,
) -> None:
    if name == "bad":
        raise RuntimeError("bad server")
    if name == "slow":
        await asyncio.Event().wait()
    async with mgr._lock:
        mgr._tools.append(
            McpTool(
                full_name=f"mcp__{name}__echo",
                remote_name="echo",
                _description="Echo",
                _parameters={"type": "object"},
                read_only=True,
                caller=object(),
            )
        )


@pytest.mark.asyncio
async def test_new_manager_empty_config_has_no_tools():
    mgr = await new_manager(Config(), version="test")

    assert mgr.tools() == []
    await mgr.close()


@pytest.mark.asyncio
async def test_new_manager_isolates_failed_server(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(manager_mod, "_do_connect", _fake_connect)
    cfg = Config(
        servers={
            "ok": ServerConfig(type="stdio", command="python"),
            "bad": ServerConfig(type="stdio", command="missing"),
        }
    )

    mgr = await new_manager(cfg, version="test")

    assert [t.name() for t in mgr.tools()] == ["mcp__ok__echo"]
    assert "connect server bad failed" in capsys.readouterr().err
    await mgr.close()


@pytest.mark.asyncio
async def test_new_manager_times_out_one_server(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(manager_mod, "_do_connect", _fake_connect)
    monkeypatch.setattr(manager_mod, "connect_timeout", 0.05)
    cfg = Config(servers={"slow": ServerConfig(type="stdio", command="python")})

    mgr = await new_manager(cfg, version="test")

    assert mgr.tools() == []
    assert "connect server slow timeout" in capsys.readouterr().err
    await mgr.close()


@pytest.mark.asyncio
async def test_manager_tools_returns_copy():
    mgr = Manager()
    mgr._tools.append(
        McpTool("mcp__demo__echo", "echo", "Echo", {"type": "object"}, True, object())
    )

    tools = mgr.tools()
    tools.clear()

    assert [t.name() for t in mgr.tools()] == ["mcp__demo__echo"]


@pytest.mark.asyncio
async def test_close_timeout_does_not_hang(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(manager_mod, "close_timeout", 0.05)
    mgr = Manager()

    async def stuck_task():
        await asyncio.Event().wait()

    mgr._tasks.append(asyncio.create_task(stuck_task()))

    await mgr.close()

    assert "close timeout" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_stdio_server_smoke_lists_and_calls_tool(tmp_path: Path):
    server = tmp_path / "demo_mcp_server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        'mcp = FastMCP("demo")\n'
        "@mcp.tool()\n"
        "def echo(text: str) -> str:\n"
        "    return text\n"
        'if __name__ == "__main__":\n'
        '    mcp.run("stdio")\n',
        encoding="utf-8",
    )
    cfg = Config(
        servers={
            "demo": ServerConfig(
                type="stdio",
                command=sys.executable,
                args=[str(server)],
            )
        }
    )

    mgr = await new_manager(cfg, version="test")
    try:
        tools = mgr.tools()
        assert [tool.name() for tool in tools] == ["mcp__demo__echo"]

        result = await tools[0].execute(json.dumps({"text": "hello"}))

        assert not result.is_error
        assert result.content == "hello"
    finally:
        await mgr.close()
