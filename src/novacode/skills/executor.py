"""Skill inline 与 fork 执行。"""

import asyncio
from collections.abc import Callable

from novacode.conversation import Conversation
from novacode.llm import ROLE_ASSISTANT, ROLE_USER, Message, Provider
from novacode.skills.parser import SkillDef, substitute_arguments

SYSTEM_TOOL_NAMES = frozenset({"LoadSkill"})


class SkillExecutor:
    def __init__(
        self,
        agent,
        client=None,
        protocol=None,
        *,
        conversation: Callable[[], Conversation] | None = None,
        provider_factory: Callable[[str], Provider] | None = None,
    ) -> None:
        self.agent = agent
        self.client = client
        self.protocol = protocol
        self._conversation = conversation
        self._provider_factory = provider_factory

    def execute_inline(self, skill: SkillDef, args: str) -> None:
        self.agent.activate_skill(skill.name, substitute_arguments(skill.prompt_body, args))

    async def execute_fork(self, skill: SkillDef, args: str) -> str:
        from novacode.agent.launch import launch_fork

        try:
            fork_conv = self._build_fork_context(skill.context)
            fork_conv.add_user(substitute_arguments(skill.prompt_body, args))
            provider = self.agent._provider
            if skill.model and self._provider_factory is not None:
                provider = self._provider_factory(skill.model)
            return await launch_fork(self.agent, fork_conv, provider=provider)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"[skill {skill.name} failed: {exc}]"

    def _build_fork_context(self, context: str) -> Conversation:
        main = self._conversation() if self._conversation is not None else Conversation()
        history = [
            message
            for message in main.messages()
            if message.role in (ROLE_USER, ROLE_ASSISTANT) and message.content
        ]
        if context == "recent":
            return Conversation.from_messages(history[-5:])
        if context == "full" and history:
            summary = "\n".join(f"{message.role}: {message.content}" for message in history)
            return Conversation.from_messages(
                [Message(role=ROLE_USER, content=f"## Previous conversation summary\n\n{summary}")]
            )
        return Conversation()
