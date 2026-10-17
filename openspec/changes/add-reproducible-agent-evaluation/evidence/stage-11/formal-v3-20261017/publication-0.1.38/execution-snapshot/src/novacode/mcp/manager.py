"""MCP server 连接管理。"""

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Implementation

from novacode.mcp.config import Config, ServerConfig
from novacode.mcp.tool import McpTool, adapt_tool

connect_timeout: float = 30.0
close_timeout: float = 5.0


@dataclass
class _Session:
    name: str
    session: ClientSession


class Manager:
    """持有已连接 MCP 会话与适配后的工具。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: list[_Session] = []
        self._tools: list[McpTool] = []
        self._stack = AsyncExitStack()
        self._tasks: list[asyncio.Task[None]] = []
        self._closing = asyncio.Event()
        self._closed = False

    def tools(self) -> list[McpTool]:
        return list(self._tools)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closing.set()
        try:
            if self._tasks:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=close_timeout,
                )
            await asyncio.wait_for(self._stack.aclose(), timeout=close_timeout)
        except TimeoutError:
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            print(
                f"[mcp] warn: close timeout ({close_timeout:g}s), some sessions may leak",
                file=sys.stderr,
            )


async def new_manager(cfg: Config, version: str) -> Manager:
    mgr = Manager()
    tasks = [
        asyncio.create_task(_start_one(mgr, name, server, version))
        for name, server in cfg.servers.items()
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    mgr._tools.sort(key=lambda item: item.full_name)
    return mgr


async def _start_one(mgr: Manager, name: str, srv: ServerConfig, version: str) -> None:
    ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(_connection_task(mgr, name, srv, version, ready))
    mgr._tasks.append(task)
    try:
        await asyncio.wait_for(asyncio.shield(ready), timeout=connect_timeout)
    except TimeoutError:
        task.cancel()
        print(
            f"[mcp] warn: connect server {name} timeout after {connect_timeout:g}s",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"[mcp] warn: connect server {name} failed: {exc}", file=sys.stderr)


async def _connection_task(
    mgr: Manager,
    name: str,
    srv: ServerConfig,
    version: str,
    ready: asyncio.Future[None],
) -> None:
    stack = AsyncExitStack()
    try:
        await _do_connect(mgr, name, srv, version, stack)
    except asyncio.CancelledError:
        if not ready.done():
            ready.cancel()
        try:
            await stack.aclose()
        finally:
            raise
    except Exception as exc:
        if not ready.done():
            ready.set_exception(exc)
        await stack.aclose()
        return

    if not ready.done():
        ready.set_result(None)

    try:
        await mgr._closing.wait()
    finally:
        await stack.aclose()


async def _do_connect(
    mgr: Manager,
    name: str,
    srv: ServerConfig,
    version: str,
    stack: AsyncExitStack,
) -> None:
    if srv.type == "stdio":
        params = StdioServerParameters(
            command=srv.command,
            args=srv.args,
            env={**os.environ, **srv.env},
        )
        ctx = stdio_client(params)
    else:
        ctx = streamablehttp_client(srv.url, headers=srv.headers or None)

    transport = await stack.enter_async_context(ctx)
    read, write = transport[0], transport[1]
    session = await stack.enter_async_context(
        ClientSession(
            read,
            write,
            client_info=Implementation(name="novacode", version=version),
        )
    )
    await session.initialize()
    listed = await session.list_tools()

    tools: list[McpTool] = []
    for remote_tool in getattr(listed, "tools", []) or []:
        tool = adapt_tool(name, remote_tool, session)
        if tool is not None:
            tools.append(tool)

    async with mgr._lock:
        mgr._sessions.append(_Session(name=name, session=session))
        mgr._tools.extend(tools)
