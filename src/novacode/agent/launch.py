"""Skill fork 与 SubAgent 共用的 Agent 构造入口。"""

from novacode.agent import Agent
from novacode.conversation import Conversation
from novacode.llm import Provider


async def launch_fork(
    parent: Agent,
    conv: Conversation,
    *,
    allowed_tools: list[str] | None = None,
    provider: Provider | None = None,
) -> str:
    child = Agent(
        provider or parent.provider,
        parent.registry,
        parent.version,
        parent.engine,
        context_window=parent.context_window,
        instructions=parent.instructions,
        memory_index=parent.memory_index,
        hook_engine=parent.hook_engine,
        allowed_tools=allowed_tools,
        subagent_name="skill-fork",
    )
    return await child.run_to_completion(conv, "")
