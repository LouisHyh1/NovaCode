"""MCP 工具适配测试。"""

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from novacode.mcp import tool as mcp_tool
from novacode.mcp.tool import McpTool, adapt_tool


@dataclass
class TextBlock:
    text: str


@dataclass
class ImageBlock:
    data: str = "ignored"


class StubSession:
    def __init__(self, result=None, exc: Exception | None = None, block: bool = False) -> None:
        self.result = result
        self.exc = exc
        self.block = block
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def call_tool(self, name: str, arguments: dict[str, object] | None):
        self.calls.append((name, arguments))
        if self.block:
            await asyncio.Event().wait()
        if self.exc is not None:
            raise self.exc
        return self.result


def _remote_tool(**kwargs):
    defaults = {
        "name": "echo",
        "description": "Echo input",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "annotations": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_adapt_tool_namespaces_schema_and_read_only():
    remote = _remote_tool(annotations=SimpleNamespace(readOnlyHint=True))

    tool = adapt_tool("demo", remote, StubSession())

    assert tool is not None
    assert tool.name() == "mcp__demo__echo"
    assert tool.description() == "Echo input"
    assert tool.parameters() == remote.inputSchema
    assert tool.read_only is True


def test_adapt_tool_rejects_illegal_full_name(capsys):
    tool = adapt_tool("bad.server", _remote_tool(), StubSession())

    assert tool is None
    assert "name contains illegal characters" in capsys.readouterr().err


def test_adapt_tool_uses_fallback_description_and_schema():
    tool = adapt_tool(
        "demo",
        _remote_tool(description="", inputSchema=None, annotations=None),
        StubSession(),
    )

    assert tool is not None
    assert "demo" in tool.description()
    assert tool.parameters() == {"type": "object"}
    assert tool.read_only is False


@pytest.mark.asyncio
async def test_execute_passes_json_arguments_and_joins_text(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_tool, "_is_text_content", lambda block: isinstance(block, TextBlock))
    result = SimpleNamespace(content=[TextBlock("one"), TextBlock("two")], isError=False)
    session = StubSession(result=result)
    tool = McpTool(
        full_name="mcp__demo__echo",
        remote_name="echo",
        _description="Echo input",
        _parameters={"type": "object"},
        read_only=True,
        caller=session,
    )

    out = await tool.execute(json.dumps({"text": "hi"}))

    assert out.content == "one\ntwo"
    assert out.is_error is False
    assert session.calls == [("echo", {"text": "hi"})]


@pytest.mark.asyncio
async def test_execute_maps_remote_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_tool, "_is_text_content", lambda block: isinstance(block, TextBlock))
    result = SimpleNamespace(content=[TextBlock("bad input")], isError=True)
    tool = McpTool(
        "mcp__demo__fail", "fail", "Fail", {"type": "object"}, False, StubSession(result)
    )

    out = await tool.execute("{}")

    assert out.content == "bad input"
    assert out.is_error is True


@pytest.mark.asyncio
async def test_execute_converts_protocol_exception_to_tool_error():
    tool = McpTool(
        "mcp__demo__boom",
        "boom",
        "Boom",
        {"type": "object"},
        False,
        StubSession(exc=RuntimeError("connection lost")),
    )

    out = await tool.execute("{}")

    assert out.is_error is True
    assert "MCP 工具调用失败" in out.content
    assert "connection lost" in out.content


@pytest.mark.asyncio
async def test_execute_timeout_returns_tool_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_tool, "call_timeout", 0.05)
    tool = McpTool(
        "mcp__demo__slow",
        "slow",
        "Slow",
        {"type": "object"},
        False,
        StubSession(block=True),
    )

    out = await tool.execute("{}")

    assert out.is_error is True
    assert "超时" in out.content


@pytest.mark.asyncio
async def test_execute_drops_non_text_blocks_once(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(mcp_tool, "_is_text_content", lambda block: isinstance(block, TextBlock))
    mcp_tool._non_text_warn_once.clear()
    result = SimpleNamespace(content=[TextBlock("visible"), ImageBlock()], isError=False)
    tool = McpTool(
        "mcp__demo__mixed",
        "mixed",
        "Mixed",
        {"type": "object"},
        True,
        StubSession(result),
    )

    first = await tool.execute("{}")
    second = await tool.execute("{}")

    assert first.content == "visible"
    assert second.content == "visible"
    assert capsys.readouterr().err.count("non-text content blocks") == 1


@pytest.mark.asyncio
async def test_execute_bad_json_returns_tool_error():
    tool = McpTool("mcp__demo__echo", "echo", "Echo", {"type": "object"}, True, StubSession())

    out = await tool.execute("{bad json")

    assert out.is_error is True
    assert "参数不是合法 JSON" in out.content
