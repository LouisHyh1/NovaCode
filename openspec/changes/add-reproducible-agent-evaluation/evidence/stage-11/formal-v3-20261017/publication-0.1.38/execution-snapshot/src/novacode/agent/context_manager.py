"""Agent Run 的上下文准备事务。"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from novacode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    RecoveryState,
    SessionContext,
    TriggerKind,
)
from novacode.compact.const import auto_compact_threshold
from novacode.compact.layer1 import offload_and_snip
from novacode.compact.layer2 import auto_compact, force_compact
from novacode.compact.token import estimate_tokens
from novacode.conversation import Conversation
from novacode.hook import DispatchResult
from novacode.hook import Engine as HookEngine
from novacode.hook import Event as HookEvent
from novacode.llm import Provider, ToolCall, ToolDefinition, Usage
from novacode.permission import Mode
from novacode.tool import resolve_path

DispatchHook = Callable[..., Awaitable[DispatchResult]]


@dataclass
class SessionRuntime:
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    usage_anchor: int = 0
    anchor_msg_len: int = 0
    resume_reminder: str = ""
    pending_reminders: list[str] = field(default_factory=list)
    hook_engine: HookEngine | None = None

    def reset_for_new_session(self, session: SessionContext) -> None:
        """为新 Session 重置所有跨 Turn 上下文状态。"""
        self.replacement = ContentReplacementState()
        self.recovery = RecoveryState()
        self.auto_tracking = CompactCircuitBreaker()
        self.session = session
        self.usage_anchor = 0
        self.anchor_msg_len = 0
        self.resume_reminder = ""
        self.pending_reminders.clear()

    def append_reminders(self, reminders: list[str]) -> None:
        self.pending_reminders.extend(reminder for reminder in reminders if reminder)

    def take_reminders(self) -> list[str]:
        reminders = list(self.pending_reminders)
        self.pending_reminders.clear()
        return reminders

    async def reset_hooks_for_new_session(self) -> None:
        if self.hook_engine is not None:
            await self.hook_engine.reset_for_new_session()


@dataclass(frozen=True)
class ContextResult:
    before_tokens: int
    after_tokens: int
    offloaded: bool
    summarized: bool
    accepted: bool = False
    disabled: bool = False


class CompressionDisabledError(RuntimeError):
    """手动入口明确返回策略禁用，不冒充完成。"""


class ContextManager:
    """隐藏 Layer 1、Layer 2 与恢复状态的上下文模块。"""

    def __init__(
        self,
        provider: Provider,
        runtime: SessionRuntime,
        *,
        context_window: int,
        dispatch_hook: DispatchHook,
        compression_enabled: bool = True,
        observer: Callable[[TriggerKind, ContextResult], None] | None = None,
    ) -> None:
        self._provider = provider
        self.runtime = runtime
        self.context_window = context_window
        self._dispatch_hook = dispatch_hook
        self.compression_enabled = compression_enabled
        self._observer = observer

    def _result(self, trigger: TriggerKind, result: ContextResult) -> ContextResult:
        if self._observer is not None:
            self._observer(trigger, result)
        return result

    async def prepare(
        self,
        conv: Conversation,
        tool_defs: list[ToolDefinition],
        trigger: TriggerKind,
        mode: Mode,
    ) -> ContextResult:
        before_messages = conv.messages()
        estimated = self.estimate(conv)
        if not self.compression_enabled:
            return self._result(
                trigger, ContextResult(estimated, estimated, False, False, disabled=True)
            )
        layer1_messages = offload_and_snip(
            before_messages,
            self.runtime.replacement,
            self.runtime.session,
        )
        offloaded = layer1_messages != before_messages
        conv.replace_history(layer1_messages)
        layer1_tokens = estimate_tokens(
            self.runtime.usage_anchor,
            layer1_messages,
            self.runtime.anchor_msg_len,
        )
        should_summarize = trigger is not TriggerKind.AUTO or (
            layer1_tokens >= auto_compact_threshold(self.context_window)
            and not self.runtime.auto_tracking.tripped()
        )
        if not should_summarize:
            return self._result(trigger, ContextResult(estimated, layer1_tokens, offloaded, False))

        await self._dispatch_hook(HookEvent.PRE_COMPACT, mode, trigger=trigger.value)
        compact_input = self._manage_input(
            conv,
            tool_defs,
            trigger,
            estimated if trigger is TriggerKind.MANUAL else layer1_tokens,
        )
        if trigger is TriggerKind.AUTO:
            new_messages, before_tokens, after_tokens = await auto_compact(compact_input)
            accepted = after_tokens < before_tokens
        else:
            new_messages, before_tokens, after_tokens = await force_compact(compact_input)
            accepted = True
        if accepted:
            conv.replace_history(new_messages)
            self.runtime.usage_anchor = estimate_tokens(0, conv.messages(), 0)
            self.runtime.anchor_msg_len = conv.length()
        await self._dispatch_hook(
            HookEvent.POST_COMPACT,
            mode,
            trigger=trigger.value,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            accepted=accepted,
        )
        return self._result(
            trigger,
            ContextResult(
                before_tokens=before_tokens,
                after_tokens=after_tokens,
                offloaded=offloaded,
                summarized=True,
                accepted=accepted,
            ),
        )

    def _manage_input(
        self,
        conv: Conversation,
        tool_defs: list[ToolDefinition],
        trigger: TriggerKind,
        estimated: int,
    ) -> ManageInput:
        return ManageInput(
            conv=conv,
            provider=self._provider,
            model=self._provider.model,
            context_window=self.context_window,
            tool_defs=tool_defs,
            replacement=self.runtime.replacement,
            recovery=self.runtime.recovery,
            auto_tracking=self.runtime.auto_tracking,
            session=self.runtime.session,
            usage_anchor=self.runtime.usage_anchor,
            anchor_msg_len=self.runtime.anchor_msg_len,
            estimated_token=estimated,
            trigger=trigger,
        )

    def estimate(self, conv: Conversation) -> int:
        """基于最近锚点估算当前上下文，新增消息只累计一次。"""
        return estimate_tokens(
            self.runtime.usage_anchor,
            conv.messages(),
            self.runtime.anchor_msg_len,
        )

    def record_usage(self, usage: Usage, conv: Conversation) -> None:
        """记录 Provider 用量锚点，后续仅估算新增消息。"""
        from novacode.compact.token import usage_anchor

        self.runtime.usage_anchor = usage_anchor(usage)
        self.runtime.anchor_msg_len = conv.length()

    async def record_read_file(self, call: ToolCall, result: object) -> None:
        """把成功读取的文件原文纳入溢出恢复快照。"""
        if call.name != "read_file" or getattr(result, "is_error", False):
            return
        try:
            data = json.loads(call.input or "{}")
            path = data.get("path")
            if not path:
                return
            absolute = Path(resolve_path(path))
            raw = await asyncio.to_thread(absolute.read_bytes)
        except (OSError, json.JSONDecodeError, TypeError):
            return
        self.runtime.recovery.record_file(
            str(absolute),
            raw.decode("utf-8", errors="replace"),
        )
