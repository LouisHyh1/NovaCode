"""NovaCode CLI entry — config loading and TUI startup."""

import argparse
import asyncio
import logging
import os
import sys
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

from novacode import __version__, hook
from novacode import mcp as mcp_client
from novacode.compact import new_session_context
from novacode.instructions import InstructionLoader
from novacode.llm import Message, Request, new_provider
from novacode.memory import (
    ManageMemoryTool,
    MemoryExtractor,
    MemoryGovernor,
    MemoryKind,
    MemoryStore,
    render_memory_indexes,
)
from novacode.memory.prompts import parse_actions
from novacode.session import SessionWriter, clean_expired_async, load_session
from novacode.subagent import load_catalog as load_subagent_catalog
from novacode.task import Manager as TaskManager
from novacode.task import TaskStopTool
from novacode.tool import Registry
from novacode.tui.app import NovaCodeApp
from novacode.tui.driver import NoAltScreenDriver

logger = logging.getLogger(__name__)


def main() -> None:
    code = asyncio.run(_amain())
    if code:
        raise SystemExit(code)


async def _amain() -> int:
    team_member_args = _parse_team_member_args(sys.argv[1:])
    if "--version" in sys.argv:
        print(__version__)
        return 0
    if "--help" in sys.argv or "-h" in sys.argv:
        print("usage: nova [--version] [--help]")
        return 0

    if team_member_args is not None:
        os.chdir(team_member_args.worktree)
    cwd = os.getcwd()
    root = Path(cwd).resolve()
    project_path = os.path.join(cwd, ".novacode", "config.yaml")
    user_path = os.path.join(os.path.expanduser("~"), ".novacode", "config.yaml")
    from novacode.config import ConfigError, load

    explicit_path = team_member_args.config if team_member_args is not None else ""
    if explicit_path and Path(explicit_path).exists():
        try:
            cfg = load(explicit_path)
        except ConfigError as e:
            print(f"Config error: {e}", file=sys.stderr)
            return 1
    elif Path(project_path).exists():
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

    sessions_dir = root / ".novacode" / "sessions"
    session_context = new_session_context(str(root))
    instructions = InstructionLoader(root, _user_novacode_root()).load()
    user_store = MemoryStore(
        _user_novacode_root() / "memory",
        frozenset({MemoryKind.USER, MemoryKind.FEEDBACK}),
    )
    project_store = MemoryStore(
        root / ".novacode" / "memory",
        frozenset({MemoryKind.PROJECT, MemoryKind.REFERENCE}),
    )
    memory_index = await _load_memory_indexes(user_store, project_store)
    memory_cache = [memory_index]
    try:
        writer = SessionWriter(sessions_dir, session_context.session_id, "")
    except Exception as exc:
        print(f"Session writer error: {exc}", file=sys.stderr)
        return 1
    extractor = MemoryExtractor(
        user_store,
        project_store,
        lambda value: memory_cache.__setitem__(0, value),
    )

    from novacode.permission.engine import new_engine
    from novacode.tool import new_default_registry

    # 构造权限引擎
    root_text = str(root)
    engine, engine_err = new_engine(root_text)
    if engine_err is not None:
        print(f"权限引擎降级: {engine_err}", file=sys.stderr)
    hook_engine = hook.load(root)

    registry = new_default_registry()
    registry.register(
        ManageMemoryTool(
            user_store,
            project_store,
            lambda value: memory_cache.__setitem__(0, value),
        )
    )
    task_mgr = TaskManager()
    from novacode.team import Manager as TeamManager
    from novacode.team.registry import AgentNameRegistry

    name_registry = AgentNameRegistry()
    task_mgr.set_name_registry(name_registry)
    subagent_catalog = load_subagent_catalog(root)
    worktree_cleanup_task = None
    try:
        from novacode.worktree import Manager as WorktreeManager

        worktree_mgr = WorktreeManager(root)
    except Exception as exc:
        print(f"Worktree 管理器降级: {exc}", file=sys.stderr)
        worktree_mgr = None
    else:
        worktree_cleanup_task = asyncio.create_task(
            worktree_mgr.sweep_stale(datetime.now() - timedelta(hours=24))
        )
    team_mgr = TeamManager(Path.home(), root, worktree_mgr, task_mgr, name_registry)
    team_mgr.configure_spawn(
        subagent_catalog,
        fork_teammate=cfg.features.fork_teammate,
    )
    task_mgr.on_task_done(team_mgr.handle_task_done)
    from novacode.team.tools import (
        SendMessageTool,
        TaskCreateTool,
        TaskGetTool,
        TaskListTool,
        TaskUpdateTool,
        TeamCreateTool,
        TeamDeleteTool,
    )

    registry.register(TaskListTool(team_mgr, task_mgr))
    registry.register(TaskGetTool(team_mgr, task_mgr))
    registry.register(TaskStopTool(task_mgr))
    registry.register(SendMessageTool(team_mgr, task_mgr))
    registry.register(TeamCreateTool(team_mgr))
    registry.register(TeamDeleteTool(team_mgr))
    registry.register(TaskCreateTool(team_mgr))
    registry.register(TaskUpdateTool(team_mgr))
    from novacode.agent.agent_tool import AgentTool

    registry.register(
        AgentTool(
            subagent_catalog,
            task_mgr,
            bg_enabled=cfg.effective_enable_subagent_background(),
            worktree_mgr=worktree_mgr,
            team_hook=team_mgr,
        )
    )
    try:
        mcp_cfg = mcp_client.load_config(root_text)
        mcp_mgr = await mcp_client.new_manager(mcp_cfg, version=__version__)
    except Exception:
        await asyncio.to_thread(writer.close)
        await hook_engine.close()
        raise
    app = None
    try:
        _register_mcp_tools(registry, mcp_mgr, mcp_cfg)
        if team_member_args is not None:
            from novacode.cli_team_member import run_team_member

            await run_team_member(
                team_member_args,
                config=cfg,
                registry=registry,
                team_manager=team_mgr,
                catalog=subagent_catalog,
                engine=engine,
                hook_engine=hook_engine,
            )
            return 0
        app_holder = {}
        queued_notices: list[str] = []

        def notify(notice: str) -> None:
            current = app_holder.get("app")
            if current is None:
                queued_notices.append(notice)
            else:
                current.notify_background(notice)

        governance_provider = new_provider(cfg.providers[0])
        governor = MemoryGovernor(
            sessions_dir,
            (user_store, project_store),
            _restricted_governance_runner(governance_provider),
            notify,
        )
        app = NovaCodeApp(
            cfg.providers,
            registry,
            __version__,
            driver_class=NoAltScreenDriver,
            engine=engine,
            hook_engine=hook_engine,
            project_root=root,
            session_context=session_context,
            writer=writer,
            extractor=extractor,
            governor=governor,
            instructions=instructions,
            memory_index=lambda: memory_cache[0],
            task_mgr=task_mgr,
            subagent_catalog=subagent_catalog,
            worktree_mgr=worktree_mgr,
            team_mgr=team_mgr,
            coordinator_mode=_coordinator_enabled(cfg),
        )
        app_holder["app"] = app
        for notice in queued_notices:
            app.notify_background(notice)
        app.cleanup_task = asyncio.create_task(clean_expired_async(sessions_dir, datetime.now(UTC)))
        try:
            governor.maybe_schedule(datetime.now(UTC))
        except Exception as exc:
            logger.warning("memory governor scheduling failed: %s", type(exc).__name__)
        await app.run_async()
    finally:
        if app is not None and hasattr(app, "_shutdown_resources"):
            if hasattr(app, "end_session"):
                await app.end_session()
            await app._shutdown_resources()
        else:
            await asyncio.to_thread(writer.close)
        await mcp_mgr.close()
        await hook_engine.close()
        if worktree_cleanup_task is not None:
            await asyncio.gather(worktree_cleanup_task, return_exceptions=True)
    return 0


def _parse_team_member_args(argv: list[str]):
    if "--team-member" not in argv:
        return None
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--team-member", action="store_true")
    parser.add_argument("--team", required=True)
    parser.add_argument("--member", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--agent-type", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--plan-mode", action="store_true")
    parser.add_argument("--config", default="")
    return parser.parse_args(argv)


def _coordinator_enabled(config) -> bool:
    from novacode.coordinator import is_enabled

    return is_enabled(config)


def _user_novacode_root() -> Path:
    return Path.home() / ".novacode"


async def _load_memory_indexes(user_store: MemoryStore, project_store: MemoryStore) -> str:
    stores = (user_store, project_store)
    indexes = ["", ""]
    try:
        async with AsyncExitStack() as stack:
            for store in stores:
                if store.directory.is_dir():
                    await stack.enter_async_context(store.locked(create=False))
            for index, store in enumerate(stores):
                if store.directory.is_dir():
                    indexes[index] = store.read_index_locked()
    except Exception as exc:
        logger.warning("memory index startup load failed: %s", type(exc).__name__)
        return ""
    return render_memory_indexes(indexes[0], indexes[1])


def _restricted_governance_runner(provider):
    async def run(**kwargs):
        session_blocks: list[str] = []
        for info in kwargs["sessions"]:
            loaded = load_session(info.path)
            lines = [
                f"{message.role}: {message.content}"
                for message in loaded.messages
                if message.content
            ]
            session_blocks.append(f"Session {info.session_id}:\n" + "\n".join(lines))

        target = Path(kwargs["target_directory"])
        note_blocks = [
            f"File {path.name}:\n{path.read_text(encoding='utf-8')}"
            for path in sorted(target.glob("*.md"))
            if path.is_file()
        ]
        content = (
            f"{kwargs['prompt']}\nAllowed kinds: "
            f"{', '.join(sorted(kind.value for kind in kwargs['allowed_kinds']))}\n\n"
            f"Indexes:\n{'\n\n'.join(kwargs['indexes'])}\n\n"
            f"Target notes:\n{'\n\n'.join(note_blocks)}\n\n"
            f"Sessions:\n{'\n\n'.join(session_blocks)}\n\n"
            "Return only a JSON array of create, update, delete, or no-op actions."
        )
        response: list[str] = []
        async for event in provider.stream(
            Request(messages=[Message(role="user", content=content)], tools=[])
        ):
            if event.err is not None:
                raise event.err
            if event.tool_calls:
                raise ValueError("restricted governance provider requested tools")
            if event.text:
                response.append(event.text)
        return parse_actions("".join(response))

    return run


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
