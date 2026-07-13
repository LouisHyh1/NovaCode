"""NovaCode CLI entry — config loading and TUI startup."""

import asyncio
import os
import sys
from pathlib import Path

from novacode import __version__
from novacode import mcp as mcp_client
from novacode.tool import Registry
from novacode.tui.app import NovaCodeApp
from novacode.tui.driver import NoAltScreenDriver


def main() -> None:
    code = asyncio.run(_amain())
    if code:
        raise SystemExit(code)


async def _amain() -> int:
    if "--version" in sys.argv:
        print(__version__)
        return 0

    cwd = os.getcwd()
    project_path = os.path.join(cwd, ".novacode", "config.yaml")
    user_path = os.path.join(os.path.expanduser("~"), ".novacode", "config.yaml")
    from novacode.config import ConfigError, load

    if Path(project_path).exists():
        try:
            cfg = load(project_path)
        except ConfigError as e:
            print(f"Config error: {e}", file=sys.stderr)
            return 1
    elif Path(user_path).exists():
        try:
            cfg = load(user_path)
        except ConfigError as e:
            print(f"Config error: {e}", file=sys.stderr)
            return 1
    else:
        print(f"Config error: config file not found: {user_path}", file=sys.stderr)
        return 1

    from novacode.permission.engine import new_engine
    from novacode.tool import new_default_registry

    # 构造权限引擎
    root = str(Path.cwd().resolve())
    engine, engine_err = new_engine(root)
    if engine_err is not None:
        print(f"权限引擎降级: {engine_err}", file=sys.stderr)

    registry = new_default_registry()
    mcp_cfg = mcp_client.load_config(root)
    mcp_mgr = await mcp_client.new_manager(mcp_cfg, version=__version__)
    try:
        _register_mcp_tools(registry, mcp_mgr, mcp_cfg)
        app = NovaCodeApp(
            cfg.providers,
            registry,
            __version__,
            driver_class=NoAltScreenDriver,
            engine=engine,
        )
        await app.run_async()
    finally:
        await mcp_mgr.close()
    return 0


def _register_mcp_tools(
    registry: Registry,
    manager: mcp_client.Manager,
    cfg: mcp_client.Config | None = None,
) -> None:
    """把已发现的 MCP 工具注册进现有工具中心。"""
    registered: list[str] = []
    for t in manager.tools():
        try:
            registry.register(t)
        except ValueError as exc:
            print(f"[mcp] warn: skip tool {t.name()}: {exc}", file=sys.stderr)
        else:
            registered.append(t.name())

    if registered:
        names = ", ".join(registered)
        print(f"[mcp] info: registered {len(registered)} tool(s): {names}", file=sys.stderr)
        return

    if cfg is not None and cfg.servers:
        servers = ", ".join(cfg.servers)
        print(
            f"[mcp] warn: configured {len(cfg.servers)} server(s) but registered 0 tool(s): "
            f"{servers}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
