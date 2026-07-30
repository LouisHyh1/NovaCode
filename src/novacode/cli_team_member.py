"""Pane 后端的无 TUI Team 队员自治循环。"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

from novacode.agent import Agent, Phase
from novacode.agent.team_hook import TeammateContext
from novacode.conversation import Conversation
from novacode.permission import Mode
from novacode.session import SessionWriter, load_session
from novacode.team.mailbox import Box, Message
from novacode.tool.filter import FilterParams, apply_agent_tool_filter


async def _print_events(agent: Agent, conversation: Conversation, task: str) -> None:
    events: asyncio.Queue = asyncio.Queue()
    running = asyncio.create_task(agent.run_to_completion(conversation, task, events))
    try:
        while True:
            event = await events.get()
            if event.text:
                print(event.text, end="", flush=True)
            if event.tool is not None and event.tool.phase is Phase.START:
                print(f"\n● {event.tool.name}({event.tool.args})", flush=True)
            if event.err is not None:
                print(f"\n[team-member error] {event.err}", file=sys.stderr, flush=True)
            if event.done:
                break
        await running
    finally:
        if not running.done():
            running.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running
    print("\n" + "─" * 60, flush=True)


async def run_team_member(args, *, config, registry, team_manager, catalog, engine, hook_engine):
    team = team_manager.get(args.team)
    if team is None:
        raise RuntimeError(f"Team 不存在: {args.team}")
    member = team.member_by_name(args.member)
    if member is None:
        # Lead 可能尚未完成 add_member，短暂等待 config 落盘。
        for _ in range(20):
            await asyncio.sleep(0.1)
            team_manager._load()
            team = team_manager.get(args.team)
            member = team.member_by_name(args.member) if team is not None else None
            if member is not None:
                break
    if team is None or member is None:
        raise RuntimeError(f"Team 成员不存在: {args.member}")

    definition = catalog.resolve(args.agent_type or "general-purpose")
    if definition is None:
        raise RuntimeError(f"未知 subagent_type: {args.agent_type}")
    from novacode.llm import new_provider

    provider_cfg = config.providers[0]
    provider = new_provider(provider_cfg)
    all_names = [item.name for item in registry.definitions()]
    allowed = apply_agent_tool_filter(
        FilterParams(
            all=all_names,
            source=int(definition.source),
            background=False,
            allowed=definition.tools,
            disallowed=definition.disallowed_tools,
            teammate=True,
        )
    )
    session_path = Path(args.session_dir)
    loaded_messages = []
    if session_path.is_file() and session_path.stat().st_size:
        loaded_messages = load_session(session_path).messages
    writer = SessionWriter(session_path.parent, session_path.stem, provider.model)
    conversation = Conversation.from_messages(
        loaded_messages,
        writer.append_message,
        writer.append_compaction,
    )
    box = Box(team.mailbox_dir)
    teammate_context = TeammateContext(
        team_name=team.sanitized_name,
        member_name=args.member,
        agent_id=args.agent_id,
        backend_type=member.backend_type.value,
        mailbox=box,
        team_manager=team_manager,
    )
    suffix = (
        "IMPORTANT: You are running as an agent in a team. "
        "You MUST use SendMessage to communicate results to your team."
    )
    system_prompt = f"{definition.system_prompt}\n\n{suffix}"
    agent = Agent(
        provider,
        registry,
        engine=engine,
        hook_engine=hook_engine,
        system_prompt=system_prompt,
        max_turns=definition.max_turns,
        permission_mode=Mode.PLAN if args.plan_mode else definition.permission_mode,
        dont_ask=True,
        allowed_tools=allowed,
        subagent_name=definition.name,
        teammate_context=teammate_context,
    )
    agent.runtime.append_reminders(
        [
            "<team-context>\n"
            f"team: {team.sanitized_name}\n你的成员名: {args.member}\n"
            f"你的 agent_id: {args.agent_id}\nworktree 目录: {args.worktree}\n"
            "</team-context>"
        ]
    )
    wake_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        loop.add_reader(sys.stdin.fileno(), wake_event.set)
    print(
        f"[team-member] {args.member} · team={team.sanitized_name} · "
        f"agent={args.agent_id} · cwd={Path.cwd()}",
        flush=True,
    )
    try:
        while Path(team.mailbox_dir).is_dir():
            _, unread = await box.read_unread(args.agent_id)
            if not unread:
                wake_event.clear()
                try:
                    await asyncio.wait_for(wake_event.wait(), timeout=2.0)
                except TimeoutError:
                    pass
                continue
            task = "请处理 Team 邮箱中的新消息，并使用 SendMessage 向相关成员报告结果。"
            await team.set_member_active(args.member, True)
            await _print_events(agent, conversation, task)
            await team.set_member_active(args.member, False)
            await box.write(
                team.lead_agent_id,
                Message(from_=args.member, text=f"[idle] {args.member} (reason: available)"),
            )
    finally:
        with contextlib.suppress(Exception):
            loop.remove_reader(sys.stdin.fileno())
        writer.close()
