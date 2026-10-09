"""产品入口共用的 Agent 与 Session 装配，不拥有 Provider。"""

from collections.abc import Callable
from typing import Any

from novacode.agent import Agent
from novacode.config import ProviderConfig, effective_context_window
from novacode.llm import Provider
from novacode.permission import Mode
from novacode.session import SessionService
from novacode.tool import Registry


def assemble_agent(
    provider: Provider,
    registry: Registry,
    config: ProviderConfig,
    session: SessionService,
    *,
    factory: Callable[..., Agent] = Agent,
    bind_model: bool = True,
    **options: Any,
) -> Agent:
    if bind_model:
        session.bind_model(provider.model)
    agent = factory(provider, registry, context_window=effective_context_window(config), **options)
    session.bind_agent(
        agent, lambda: agent._tool_definitions(agent.permission_mode or Mode.DEFAULT)
    )
    return agent
