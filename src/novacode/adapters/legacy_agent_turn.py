"""把现有 Agent 事件流适配为新的 Turn Engine 合同。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from novacode.agent import Event
from novacode.conversation import Conversation
from novacode.permission import Mode
from novacode.runtime.errors import ValidationError
from novacode.runtime.turn import (
    ApprovalTurnEvent,
    CancelledTurnEvent,
    DoneTurnEvent,
    ErrorTurnEvent,
    TextTurnEvent,
    ToolTurnEvent,
    TurnEvent,
    TurnRequest,
    UsageTurnEvent,
)


class LegacyAgent(Protocol):
    def run(
        self,
        conversation: Conversation,
        mode: Mode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event]: ...


class LegacyAgentTurnEngine:
    """直接消费旧 Agent 状态，不维护另一份运行状态。"""

    def __init__(self, agent: LegacyAgent) -> None:
        self._agent = agent

    async def run(self, request: TurnRequest) -> AsyncIterator[TurnEvent]:
        conversation, mode = _prepare_legacy_request(request)
        try:
            async for event in self._agent.run(conversation, mode, request.cancel):
                if request.cancel.is_set():
                    yield CancelledTurnEvent()
                    return
                for item in _map_event(event):
                    yield item
                    if isinstance(item, (DoneTurnEvent, ErrorTurnEvent)):
                        return
        except asyncio.CancelledError:
            request.cancel.set()
            yield CancelledTurnEvent()
            return
        if request.cancel.is_set():
            yield CancelledTurnEvent()


def _prepare_legacy_request(request: TurnRequest) -> tuple[Conversation, Mode]:
    if not isinstance(request.conversation, Conversation):
        raise ValidationError("Legacy Agent 需要 Conversation 会话对象")
    conversation = request.conversation
    if request.user_input:
        conversation.add_user(request.user_input)
    try:
        mode = Mode(request.mode)
    except ValueError as exc:
        raise ValidationError(f"未知权限模式: {request.mode}") from exc
    return conversation, mode


def _map_event(event: Event) -> tuple[TurnEvent, ...]:
    mapped: list[TurnEvent] = []
    if event.text:
        mapped.append(TextTurnEvent(event.text))
    if event.notice:
        mapped.append(TextTurnEvent(event.notice))
    if event.tool is not None:
        mapped.append(
            ToolTurnEvent(
                name=event.tool.name,
                args=event.tool.args,
                phase=event.tool.phase.value,
                result=event.tool.result,
                is_error=event.tool.is_error,
            )
        )
    if event.approval is not None:
        mapped.append(
            ApprovalTurnEvent(
                name=event.approval.name,
                args=event.approval.args,
                reason=event.approval.reason,
                response=event.approval.respond,
            )
        )
    if event.usage is not None:
        mapped.append(
            UsageTurnEvent(
                input=event.usage.input,
                output=event.usage.output,
                cache_write=event.usage.cache_write,
                cache_read=event.usage.cache_read,
            )
        )
    if event.err is not None:
        mapped.append(
            ErrorTurnEvent(
                error_category=type(event.err).__name__,
                message=str(event.err),
                error=event.err,
            )
        )
    elif event.done:
        mapped.append(DoneTurnEvent(event.memory_turn))
    return tuple(mapped)
