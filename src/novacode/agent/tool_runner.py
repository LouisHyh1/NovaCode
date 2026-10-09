"""一次模型工具调用批次的完整执行事务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from novacode.agent import (
    ApprovalRequest,
    ApprovalUpgrader,
    Event,
    Phase,
    ToolEvent,
)
from novacode.conversation import Conversation
from novacode.hook import DispatchResult
from novacode.hook import Event as HookEvent
from novacode.llm import ToolCall, ToolResult
from novacode.permission import Decision, Mode, Outcome
from novacode.permission.engine import Engine
from novacode.permission.persist import persist_local_allow
from novacode.privacy import redact
from novacode.tool import DEFAULT_TIMEOUT, Registry

if TYPE_CHECKING:
    from novacode.agent import Agent

DispatchHook = Callable[..., Awaitable[DispatchResult]]
RecordRead = Callable[[ToolCall, object], Awaitable[None]]

logger = logging.getLogger(__name__)


def _args_preview(args: str) -> str:
    return args[:80] + "…" if len(args) > 80 else args


async def _cancel_and_wait(task: asyncio.Future[Any]) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@dataclass(frozen=True)
class ToolRunResult:
    results: list[ToolResult]
    completed: bool
    all_unknown: bool


@dataclass(frozen=True)
class ToolRunUpdate:
    event: Event | None = None
    result: ToolRunResult | None = None


class ToolRunner:
    """隐藏分组、并发与工具结果归一化的工具模块。"""

    def __init__(
        self,
        registry: Registry,
        *,
        dispatch_hook: DispatchHook,
        engine: Engine | None = None,
        dont_ask: bool = False,
        approval_upgrader: ApprovalUpgrader | None = None,
        subagent_name: str = "",
        allowed_tools: list[str] | None = None,
        owner: Agent | None = None,
        record_read: RecordRead | None = None,
        observer: Callable[[ToolEvent], None] | None = None,
    ) -> None:
        self._registry = registry
        self._dispatch_hook = dispatch_hook
        self._engine = engine
        self._dont_ask = dont_ask
        self._approval_upgrader = approval_upgrader
        self._subagent_name = subagent_name
        self._allowed_tools = None if allowed_tools is None else frozenset(allowed_tools)
        self._owner = owner
        self._record_read = record_read
        self._observer = observer
        self._sequence = 0
        self._failed_calls: dict[tuple[str, str], str] = {}

    def set_allowed_tools(self, names: list[str]) -> None:
        self._allowed_tools = frozenset(names)

    async def run(
        self,
        calls: list[ToolCall],
        conversation: Conversation,
        cancel: asyncio.Event,
        mode: Mode,
    ) -> AsyncIterator[ToolRunUpdate]:
        results: list[ToolResult | None] = [None] * len(calls)
        index = 0
        while index < len(calls):
            end = self._batch_end(calls, index)
            starts = []
            for call in calls[index:end]:
                self._sequence += 1
                event = self._tool_event(call, Phase.START)
                starts.append(event.tool)
                yield ToolRunUpdate(event=event)
            event_queue: asyncio.Queue[Event] = asyncio.Queue()
            batch = [
                asyncio.create_task(self._execute(call, conversation, cancel, mode, event_queue))
                for call in calls[index:end]
            ]
            batch_task = asyncio.gather(*batch)
            try:
                while not batch_task.done():
                    queue_task = asyncio.create_task(event_queue.get())
                    done, _ = await asyncio.wait(
                        (queue_task, batch_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if queue_task in done:
                        yield ToolRunUpdate(event=queue_task.result())
                    else:
                        await _cancel_and_wait(queue_task)
                while not event_queue.empty():
                    yield ToolRunUpdate(event=event_queue.get_nowait())
                batch_results = batch_task.result()
            except BaseException as exc:
                await self._interrupt_batch(calls[index:end], starts, batch, exc)
                raise
            finally:
                if not batch_task.done():
                    await _cancel_and_wait(batch_task)
            for offset, result in enumerate(batch_results):
                result_index = index + offset
                results[result_index] = result
                yield ToolRunUpdate(
                    event=self._tool_event(calls[result_index], Phase.END, result, starts[offset])
                )
            index = end

        finalized = [result for result in results if result is not None]
        yield ToolRunUpdate(
            result=ToolRunResult(
                results=finalized,
                completed=not cancel.is_set(),
                all_unknown=all(self._registry.get(call.name) is None for call in calls),
            )
        )

    def _batch_end(self, calls: list[ToolCall], index: int) -> int:
        end = index + 1
        if self._registry.is_read_only(calls[index].name):
            while end < len(calls) and self._registry.is_read_only(calls[end].name):
                end += 1
        return end

    async def _interrupt_batch(
        self,
        calls: list[ToolCall],
        starts: list[ToolEvent | None],
        batch: list[asyncio.Task[ToolResult]],
        exc: BaseException,
    ) -> None:
        for task in batch:
            task.cancel()
        await asyncio.gather(*batch, return_exceptions=True)
        for call, start in zip(calls, starts, strict=True):
            self._tool_event(
                call,
                Phase.END,
                ToolResult(
                    call.id,
                    "",
                    is_error=True,
                    execution="interrupted",
                    error_type=type(exc).__name__,
                ),
                start,
            )

    def _tool_event(
        self,
        call: ToolCall,
        phase: Phase,
        result: ToolResult | None = None,
        start: ToolEvent | None = None,
    ) -> Event:
        key = self._retry_key(call)
        try:
            full_args = json.dumps(redact(json.loads(call.input)), ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            full_args = redact(call.input)
        tool_event = ToolEvent(
            name=call.name,
            args=_args_preview(call.input),
            phase=phase,
            result="" if result is None else result.content,
            is_error=False if result is None else result.is_error,
            call_id=call.id,
            invocation_id=uuid4().hex if start is None else start.invocation_id,
            sequence=self._sequence if start is None else start.sequence,
            full_args=full_args,
            started_monotonic=time.monotonic() if start is None else start.started_monotonic,
            ended_monotonic=None if result is None else result.ended_monotonic or time.monotonic(),
            authorization="pending" if result is None else result.authorization,
            execution="pending" if result is None else result.execution,
            error_type="" if result is None else result.error_type,
            retry_of=self._failed_calls.get(key) if start is None else start.retry_of,
        )
        if result is not None:
            if result.is_error:
                self._failed_calls[key] = tool_event.invocation_id
            else:
                self._failed_calls.pop(key, None)
        if self._observer is not None:
            self._observer(tool_event)
        return Event(tool=tool_event)

    @staticmethod
    def _retry_key(call: ToolCall) -> tuple[str, str]:
        try:
            canonical = json.dumps(json.loads(call.input), sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError):
            canonical = call.input
        return call.name, hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def _execute(
        self,
        call: ToolCall,
        conversation: Conversation,
        cancel: asyncio.Event,
        mode: Mode,
        event_queue: asyncio.Queue[Event],
    ) -> ToolResult:
        result = await self._precheck(call, mode)
        if result is None:
            result = await self._authorize(call, cancel, mode, event_queue)
        if result is None:
            result = await self._execute_allowed(call, conversation, cancel)
            result.authorization = "allowed"
        else:
            result.authorization = "denied"
            result.execution = "not-executed"
            result.error_type = "PolicyDenied"
        await self._post_hook(call, result, mode)
        result.ended_monotonic = time.monotonic()
        return result

    async def _precheck(self, call: ToolCall, mode: Mode) -> ToolResult | None:
        hook = await self._dispatch_hook(
            HookEvent.PRE_TOOL_USE,
            mode,
            tool_name=call.name,
            tool_input=self._tool_input(call),
        )
        if hook.blocked:
            return ToolResult(
                call.id,
                f"[hook {hook.blocking_hook_name}] {hook.reason}",
                is_error=True,
            )
        if self._allowed_tools is not None and call.name not in self._allowed_tools:
            return ToolResult(
                call.id,
                f"工具 {call.name} 对当前 SubAgent 不可用",
                is_error=True,
            )
        if mode == Mode.PLAN and not self._registry.is_read_only(call.name):
            return ToolResult(
                call.id,
                f"[计划模式拒绝] {call.name} 未执行。"
                "计划模式下只允许只读操作，文件系统未做任何修改。",
                is_error=True,
                is_policy_denial=True,
            )
        return None

    async def _authorize(
        self,
        call: ToolCall,
        cancel: asyncio.Event,
        mode: Mode,
        event_queue: asyncio.Queue[Event],
    ) -> ToolResult | None:
        if self._engine is None:
            return None
        decision, reason = self._engine.check(
            mode,
            call,
            self._registry.is_read_only(call.name),
        )
        if decision == Decision.DENY:
            return ToolResult(call.id, reason, is_error=True)
        if decision != Decision.ASK or self._dont_ask:
            return None
        outcome = await self._request_approval(call, reason, mode, cancel, event_queue)
        if outcome == Outcome.DENY_ONCE:
            return ToolResult(
                call.id,
                f"[已拒绝] {call.name} 未执行。"
                f"用户拒绝了此操作：{reason}。"
                "该操作未对文件系统产生任何影响。",
                is_error=True,
            )
        if outcome == Outcome.ALLOW_FOREVER:
            try:
                persist_local_allow(self._engine, call)
            except Exception as exc:
                logger.warning("持久化规则失败: %s", exc)
        return None

    async def _execute_allowed(
        self,
        call: ToolCall,
        conversation: Conversation,
        cancel: asyncio.Event,
    ) -> ToolResult:
        if cancel.is_set():
            return ToolResult(
                call.id,
                "（已取消。）",
                is_error=True,
                execution="not-executed",
                error_type="CancelledError",
            )
        tool = self._registry.get(call.name)
        if tool is None:
            return ToolResult(
                call.id,
                f"未知工具: {call.name}",
                is_error=True,
                execution="not-executed",
                error_type="UnknownTool",
            )
        timeout = getattr(tool, "timeout", DEFAULT_TIMEOUT)
        if self._owner is None:
            execute_task = asyncio.create_task(
                self._registry.execute(call.name, call.input, timeout=timeout)
            )
        else:
            from novacode.agent.context import ExecutionContext, bind, reset

            token = bind(ExecutionContext(self._owner, conversation))
            try:
                execute_task = asyncio.create_task(
                    self._registry.execute(call.name, call.input, timeout=timeout)
                )
            finally:
                reset(token)
        cancel_task = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait(
                (execute_task, cancel_task), return_when=asyncio.FIRST_COMPLETED
            )
        except BaseException:
            await _cancel_and_wait(execute_task)
            await _cancel_and_wait(cancel_task)
            raise
        if cancel_task in done:
            await _cancel_and_wait(execute_task)
            return ToolResult(
                call.id,
                "（已取消。）",
                is_error=True,
                execution="cancelled",
                error_type="CancelledError",
            )
        await _cancel_and_wait(cancel_task)
        executed = execute_task.result()
        if self._record_read is not None:
            await self._record_read(call, executed)
        return ToolResult(
            call.id,
            executed.content,
            is_error=executed.is_error,
            execution="failed" if executed.is_error else "succeeded",
            error_type=str(executed.metadata.get("error_type", "ToolError"))
            if executed.is_error
            else "",
        )

    async def _post_hook(self, call: ToolCall, result: ToolResult, mode: Mode) -> None:
        await self._dispatch_hook(
            HookEvent.POST_TOOL_USE,
            mode,
            tool_name=call.name,
            tool_input=self._tool_input(call),
            tool_result=result.content,
            is_error=result.is_error,
        )

    @staticmethod
    def _tool_input(call: ToolCall) -> dict[str, object]:
        try:
            value = json.loads(call.input)
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    async def _request_approval(
        self,
        call: ToolCall,
        reason: str,
        mode: Mode,
        cancel: asyncio.Event,
        event_queue: asyncio.Queue[Event],
    ) -> Outcome:
        respond: asyncio.Future[Outcome] = asyncio.get_running_loop().create_future()
        await self._dispatch_hook(
            HookEvent.NOTIFICATION,
            mode,
            kind="approval",
            detail=call.name,
        )
        request = ApprovalRequest(
            name=call.name,
            args=_args_preview(call.input),
            reason=(
                f"[来自 SubAgent {self._subagent_name}] {reason}" if self._subagent_name else reason
            ),
            respond=respond,
        )
        if self._approval_upgrader is not None:
            outcome, handled = await self._approval_upgrader(request)
            if handled:
                return outcome
        await event_queue.put(Event(approval=request))
        cancel_task = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait(
                (respond, cancel_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            if not respond.done():
                respond.set_result(Outcome.DENY_ONCE)
            await _cancel_and_wait(cancel_task)
            raise
        if cancel_task in done:
            if not respond.done():
                respond.set_result(Outcome.DENY_ONCE)
            return Outcome.DENY_ONCE
        await _cancel_and_wait(cancel_task)
        return respond.result()
