"""ReAct 循环编排——模型自主多轮：想 → 调工具 → 看结果 → 边做边调整，直到任务完成。"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from novacode import prompt
from novacode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    RecoveryState,
    SessionContext,
    TriggerKind,
    manage_context,
    new_session_context,
)
from novacode.compact.const import MANUAL_SAFETY_MARGIN, auto_compact_threshold
from novacode.compact.token import estimate_tokens, usage_anchor
from novacode.conversation import Conversation
from novacode.llm import (
    PromptTooLongError,
    Provider,
    Request,
    System,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from novacode.llm import (
    Usage as LLMUsage,
)
from novacode.permission import Decision, Mode, Outcome
from novacode.permission.engine import Engine
from novacode.permission.persist import persist_local_allow
from novacode.tool import DEFAULT_TIMEOUT, Registry

logger = logging.getLogger(__name__)

MAX_ITERATIONS: int = 25
MAX_UNKNOWN_RUN: int = 3

PLAN_REMINDER_INTERVAL: int = 4

NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"


class Phase(Enum):
    START = "start"
    END = "end"


class CompactPhase(Enum):
    BEFORE_AUTO = "before_auto"
    AFTER_AUTO = "after_auto"
    BEFORE_EMERGENCY = "before_emergency"
    AFTER_EMERGENCY = "after_emergency"


@dataclass
class ToolEvent:
    """一次工具调用的开始/结束（供 TUI 渲染工具行与结果摘要）。"""

    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False


@dataclass
class ApprovalRequest:
    """人在回路——待批准的工具调用（第五层）。"""

    name: str
    args: str
    reason: str
    respond: asyncio.Future[Outcome]


@dataclass
class Usage:
    """一轮请求的 token 用量（透传 llm.Usage 语义，含缓存命中）。"""

    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class CompactEvent:
    phase: CompactPhase
    before: int = 0
    after: int = 0
    err: Exception | None = None


@dataclass
class Event:
    """Agent Loop 对外事件流元素，TUI 据非默认字段分派渲染。"""

    text: str = ""
    tool: ToolEvent | None = None
    usage: Usage | None = None
    approval: ApprovalRequest | None = None
    iter: int = 0
    notice: str = ""
    done: bool = False
    err: Exception | None = None
    compact: CompactEvent | None = None


@dataclass
class SessionRuntime:
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    usage_anchor: int = 0
    anchor_msg_len: int = 0


@dataclass
class _StreamState:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: LLMUsage | None = None
    err: Exception | None = None
    cancelled: bool = False


def _args_preview(args: str) -> str:
    """工具参数截断预览（最多 80 字符）。"""
    return args[:80] + "…" if len(args) > 80 else args


async def _cancel_and_wait(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


class Agent:
    """持有 provider、注册中心与权限引擎，执行 ReAct 循环。"""

    def __init__(
        self,
        provider: Provider,
        registry: Registry,
        version: str = "",
        engine: Engine | None = None,
        *,
        runtime: SessionRuntime | None = None,
        context_window: int = 200_000,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version
        self.engine = engine
        self._event_queue: asyncio.Queue[Event] | None = None
        self.runtime = runtime or SessionRuntime(
            replacement=ContentReplacementState(),
            recovery=RecoveryState(),
            auto_tracking=CompactCircuitBreaker(),
            session=new_session_context(str(Path.cwd())),
        )
        self.context_window = context_window
        self._run_lock = asyncio.Lock()

    def _manage_input(
        self,
        conv: Conversation,
        defs: list[ToolDefinition],
        trigger: TriggerKind,
        estimated: int,
    ) -> ManageInput:
        return ManageInput(
            conv=conv,
            provider=self._provider,
            model=self._provider.model,
            context_window=self.context_window,
            tool_defs=defs,
            replacement=self.runtime.replacement,
            recovery=self.runtime.recovery,
            auto_tracking=self.runtime.auto_tracking,
            session=self.runtime.session,
            usage_anchor=self.runtime.usage_anchor,
            anchor_msg_len=self.runtime.anchor_msg_len,
            estimated_token=estimated,
            trigger=trigger,
        )

    async def run_force_compact(
        self,
        conv: Conversation,
        tool_defs: list[ToolDefinition],
    ) -> tuple[int, int]:
        async with self._run_lock:
            estimated = estimate_tokens(0, conv.messages(), 0)
            out = await manage_context(
                self._manage_input(conv, tool_defs, TriggerKind.MANUAL, estimated)
            )
            self.runtime.usage_anchor = 0
            self.runtime.anchor_msg_len = 0
            return out.before_tokens, out.after_tokens

    async def run(
        self,
        conv: Conversation,
        mode: Mode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event]:
        env = prompt.gather_environment(self._version, self._provider.model)
        sys = prompt.build_system_prompt()
        env_text = env.render()

        if mode == Mode.PLAN:
            defs = self._registry.read_only_definitions()
        else:
            defs = self._registry.definitions()

        unknown_run = 0

        for it in range(1, MAX_ITERATIONS + 1):
            yield Event(iter=it)
            if cancel.is_set():
                self._finish_cancelled(conv)
                return

            reminder = ""
            if mode == Mode.PLAN:
                full = it == 1 or (it - 1) % PLAN_REMINDER_INTERVAL == 0
                reminder = prompt.plan_reminder(full)

            estimated = estimate_tokens(
                self.runtime.usage_anchor,
                conv.messages(),
                self.runtime.anchor_msg_len,
            )
            emit_auto = (
                estimated >= auto_compact_threshold(self.context_window)
                and not self.runtime.auto_tracking.tripped()
            )
            if emit_auto:
                yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_AUTO))
            try:
                compact_out = await manage_context(
                    self._manage_input(conv, defs, TriggerKind.AUTO, estimated)
                )
            except Exception as e:
                if emit_auto:
                    yield Event(compact=CompactEvent(phase=CompactPhase.AFTER_AUTO, err=e))
                yield Event(err=e)
                self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                return
            if emit_auto:
                yield Event(
                    compact=CompactEvent(
                        phase=CompactPhase.AFTER_AUTO,
                        before=compact_out.before_tokens,
                        after=compact_out.after_tokens,
                    )
                )

            emergency_retried = False
            while True:
                stream_state = _StreamState()
                async for ev in self._stream_once(
                    conv, defs, sys, env_text, reminder, cancel, stream_state
                ):
                    yield ev

                text = stream_state.text
                calls = stream_state.calls or []
                usage = stream_state.usage
                err = stream_state.err

                if err is None:
                    if stream_state.cancelled:
                        self._finish_cancelled(conv)
                        return
                    break

                if cancel.is_set():
                    self._finish_cancelled(conv)
                    return

                if isinstance(err, PromptTooLongError) and not emergency_retried:
                    yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_EMERGENCY))
                    try:
                        emergency_in = self._manage_input(
                            conv,
                            defs,
                            TriggerKind.EMERGENCY,
                            estimate_tokens(0, conv.messages(), 0),
                        )
                        emergency_out = await manage_context(emergency_in)
                    except Exception as e:
                        yield Event(
                            compact=CompactEvent(
                                phase=CompactPhase.AFTER_EMERGENCY,
                                err=e,
                            )
                        )
                        yield Event(err=e)
                        self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                        return
                    yield Event(
                        compact=CompactEvent(
                            phase=CompactPhase.AFTER_EMERGENCY,
                            before=emergency_out.before_tokens,
                            after=emergency_out.after_tokens,
                        )
                    )
                    self.runtime.usage_anchor = 0
                    self.runtime.anchor_msg_len = 0
                    retry_estimate = estimate_tokens(0, conv.messages(), 0)
                    if retry_estimate < self.context_window - MANUAL_SAFETY_MARGIN:
                        emergency_retried = True
                        continue

                yield Event(err=err)
                self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                return

            if usage is not None:
                self.runtime.usage_anchor = usage_anchor(usage)
                self.runtime.anchor_msg_len = conv.length()
                yield Event(
                    usage=Usage(
                        input=usage.input_tokens,
                        output=usage.output_tokens,
                        cache_write=usage.cache_write,
                        cache_read=usage.cache_read,
                    )
                )

            if not calls:
                conv.add_assistant(self._ensure_final(text))
                yield Event(done=True)
                return

            conv.add_assistant_with_tool_calls(text, calls)
            unknown_run = unknown_run + 1 if self._all_unknown(calls) else 0

            # 通过队列并行运行 _execute_batched——支持人在回路事件穿插
            self._event_queue = asyncio.Queue()
            batch_task = asyncio.create_task(self._execute_batched(calls, cancel, mode))

            # 从队列消费事件直到 batch_task 完成
            results: list[ToolResult] = []
            completed = True
            while True:
                if batch_task.done():
                    break
                queue_task = asyncio.create_task(self._event_queue.get())
                try:
                    done, _ = await asyncio.wait(
                        (queue_task, batch_task), return_when=asyncio.FIRST_COMPLETED
                    )
                    if queue_task in done:
                        yield queue_task.result()
                    else:
                        await _cancel_and_wait(queue_task)
                        break
                except asyncio.CancelledError:
                    await _cancel_and_wait(queue_task)
                    if not batch_task.done():
                        await _cancel_and_wait(batch_task)
                    raise
                except Exception:
                    await _cancel_and_wait(queue_task)
                    if not batch_task.done():
                        await _cancel_and_wait(batch_task)
                    break
            # Drain 余量事件：同步返回的 DENY 路径可能让事件留在队列里
            while True:
                try:
                    ev = self._event_queue.get_nowait()
                    yield ev
                except asyncio.QueueEmpty:
                    break

            self._event_queue = None
            try:
                results, completed = batch_task.result()
            except asyncio.CancelledError:
                results = []
                completed = False

            conv.add_tool_results(results)

            # Plan 模式硬拒绝：输出确定性收尾，不让模型自由总结误报成功
            if mode == Mode.PLAN and any(getattr(r, "is_policy_denial", False) for r in results):
                terminal_text = (
                    "计划模式已拒绝执行写入/命令操作。"
                    "未对文件系统做任何修改。"
                    "如需执行，请使用 /do 或切换到 ACCEPT_EDITS/BYPASS 模式。"
                )
                conv.add_assistant(terminal_text)
                yield Event(text=terminal_text, done=True)
                return

            if not completed:
                self._ensure_assistant_tail(conv, NOTICE_CANCELLED)
                return

            if unknown_run >= MAX_UNKNOWN_RUN:
                yield Event(notice=NOTICE_UNKNOWN_TOOLS)
                self._ensure_assistant_tail(conv, NOTICE_UNKNOWN_TOOLS)
                yield Event(done=True)
                return

        yield Event(notice=NOTICE_MAX_ITER)
        self._ensure_assistant_tail(conv, NOTICE_MAX_ITER)
        yield Event(done=True)

    async def _stream_once(
        self,
        conv: Conversation,
        defs: list,
        sys: str,
        env_text: str,
        reminder: str,
        cancel: asyncio.Event,
        state: _StreamState,
    ) -> AsyncIterator[Event]:
        req = Request(
            messages=conv.messages(),
            tools=defs,
            system=System(stable=sys, environment=env_text),
            reminder=reminder,
        )
        stream = self._provider.stream(req)
        cancel_task = asyncio.create_task(cancel.wait())
        next_task: asyncio.Task | None = None
        try:
            while True:
                next_task = asyncio.create_task(anext(stream))
                done, _ = await asyncio.wait(
                    (next_task, cancel_task), return_when=asyncio.FIRST_COMPLETED
                )
                if cancel_task in done:
                    state.cancelled = True
                    await _cancel_and_wait(next_task)
                    return

                try:
                    ev = next_task.result()
                except StopAsyncIteration:
                    return
                finally:
                    next_task = None

                if ev.err is not None:
                    state.err = ev.err
                    return
                if ev.usage is not None:
                    state.usage = ev.usage
                if ev.tool_calls:
                    state.calls = ev.tool_calls
                if ev.text:
                    state.text += ev.text
                    yield Event(text=ev.text)
        finally:
            if next_task is not None:
                await _cancel_and_wait(next_task)
            await _cancel_and_wait(cancel_task)
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    async def _execute_batched(
        self,
        calls: list[ToolCall],
        cancel: asyncio.Event,
        mode: Mode,
    ) -> tuple[list[ToolResult], bool]:
        """保序分批并发执行工具（含权限检查）。通过 _emit 发事件。"""
        results: list[ToolResult | None] = [None] * len(calls)
        i = 0
        while i < len(calls):
            if cancel.is_set():
                self._fill_cancelled(results, calls, i)
                return self._finalize_results(results, calls), False

            if self._registry.is_read_only(calls[i].name):
                j = i + 1
                while j < len(calls) and self._registry.is_read_only(calls[j].name):
                    j += 1

                done = [False] * (j - i)
                for k in range(i, j):
                    if self.engine is not None:
                        decision, reason = self.engine.check(mode, calls[k], True)
                        if decision == Decision.DENY:
                            results[k] = ToolResult(
                                tool_call_id=calls[k].id, content=reason, is_error=True
                            )
                            done[k - i] = True
                            await self._emit(
                                Event(
                                    tool=ToolEvent(
                                        name=calls[k].name,
                                        args=_args_preview(calls[k].input),
                                        phase=Phase.START,
                                    )
                                )
                            )
                        else:
                            await self._emit(
                                Event(
                                    tool=ToolEvent(
                                        name=calls[k].name,
                                        args=_args_preview(calls[k].input),
                                        phase=Phase.START,
                                    )
                                )
                            )
                    else:
                        await self._emit(
                            Event(
                                tool=ToolEvent(
                                    name=calls[k].name,
                                    args=_args_preview(calls[k].input),
                                    phase=Phase.START,
                                )
                            )
                        )

                tasks = [
                    self._run_one(k, calls[k], results, cancel)
                    for k in range(i, j)
                    if not done[k - i]
                ]
                if tasks:
                    await asyncio.gather(*tasks)

                for k in range(i, j):
                    if results[k] is not None:
                        r = results[k]
                        await self._emit(
                            Event(
                                tool=ToolEvent(
                                    name=calls[k].name,
                                    args=_args_preview(calls[k].input),
                                    phase=Phase.END,
                                    result=r.content,
                                    is_error=r.is_error,
                                )
                            )
                        )
                i = j
            else:
                await self._emit(
                    Event(
                        tool=ToolEvent(
                            name=calls[i].name,
                            args=_args_preview(calls[i].input),
                            phase=Phase.START,
                        )
                    )
                )
                r, ok = await self._run_side_effect(calls[i], cancel, mode)
                if not ok:
                    results[i] = r
                    self._fill_cancelled(results, calls, i + 1)
                    return self._finalize_results(results, calls), False
                results[i] = r
                if results[i] is not None:
                    await self._emit(
                        Event(
                            tool=ToolEvent(
                                name=calls[i].name,
                                args=_args_preview(calls[i].input),
                                phase=Phase.END,
                                result=results[i].content,
                                is_error=results[i].is_error,
                            )
                        )
                    )
                i += 1

        return self._finalize_results(results, calls), True

    async def _run_side_effect(
        self,
        call: ToolCall,
        cancel: asyncio.Event,
        mode: Mode,
    ) -> tuple[ToolResult, bool]:
        """执行一个有副作用工具调用（含权限检查）。返回 (result, ok)。

        ok=False 表示取消。
        """
        # Plan 模式防御深度：非只读工具直接拒绝，不进入引擎或审批
        if mode == Mode.PLAN and not self._registry.is_read_only(call.name):
            return (
                ToolResult(
                    tool_call_id=call.id,
                    content=f"[计划模式拒绝] {call.name} 未执行。"
                    f"计划模式下只允许只读操作，文件系统未做任何修改。",
                    is_error=True,
                    is_policy_denial=True,
                ),
                True,
            )

        if self.engine is None:
            return await self._execute_allowed(call, cancel)

        decision, reason = self.engine.check(mode, call, False)

        if decision == Decision.ALLOW:
            return await self._execute_allowed(call, cancel)

        if decision == Decision.DENY:
            return (
                ToolResult(
                    tool_call_id=call.id,
                    content=reason,
                    is_error=True,
                    is_policy_denial=(mode == Mode.PLAN),
                ),
                True,
            )

        # ASK → 人在回路
        try:
            outcome = await self._request_approval(call, reason)
        except asyncio.CancelledError:
            return (
                ToolResult(tool_call_id=call.id, content=NOTICE_CANCELLED, is_error=True),
                False,
            )

        if cancel.is_set():
            return (
                ToolResult(tool_call_id=call.id, content=NOTICE_CANCELLED, is_error=True),
                False,
            )

        if outcome == Outcome.DENY_ONCE:
            return (
                ToolResult(
                    tool_call_id=call.id,
                    content=f"[已拒绝] {call.name} 未执行。"
                    f"用户拒绝了此操作：{reason}。"
                    f"该操作未对文件系统产生任何影响。",
                    is_error=True,
                ),
                True,
            )
        elif outcome in (Outcome.ALLOW_ONCE, Outcome.ALLOW_FOREVER):
            if outcome == Outcome.ALLOW_FOREVER:
                try:
                    persist_local_allow(self.engine, call)
                except Exception as e:
                    logger.warning("持久化规则失败: %s", e)
            return await self._execute_allowed(call, cancel)

        return (
            ToolResult(tool_call_id=call.id, content="未知权限裁决", is_error=True),
            True,
        )

    async def _execute_allowed(
        self, call: ToolCall, cancel: asyncio.Event
    ) -> tuple[ToolResult, bool]:
        result = await self._execute_and_result(call, cancel)
        return result, not cancel.is_set()

    async def _execute_and_result(self, call: ToolCall, cancel: asyncio.Event) -> ToolResult:
        """执行工具并返回 ToolResult。"""
        from novacode.tool import Result as ToolExecResult

        if cancel.is_set():
            return ToolResult(tool_call_id=call.id, content=NOTICE_CANCELLED, is_error=True)

        exec_task = asyncio.create_task(
            self._registry.execute(call.name, call.input, timeout=DEFAULT_TIMEOUT)
        )
        cancel_task = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait(
                (exec_task, cancel_task), return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_task in done:
                await _cancel_and_wait(exec_task)
                return ToolResult(tool_call_id=call.id, content=NOTICE_CANCELLED, is_error=True)
            await _cancel_and_wait(cancel_task)
            r: ToolExecResult = exec_task.result()
            await self._record_read_file(call, r)
            return ToolResult(tool_call_id=call.id, content=r.content, is_error=r.is_error)
        except asyncio.CancelledError:
            await _cancel_and_wait(exec_task)
            return ToolResult(tool_call_id=call.id, content=NOTICE_CANCELLED, is_error=True)
        except Exception as e:
            return ToolResult(
                tool_call_id=call.id,
                content=f"工具 {call.name} 异常: {e}",
                is_error=True,
            )
        finally:
            await _cancel_and_wait(cancel_task)

    async def _request_approval(self, call: ToolCall, reason: str) -> Outcome:
        """发出人在回路请求事件，await Future 等待 TUI 回传用户选择。"""
        respond: asyncio.Future[Outcome] = asyncio.get_running_loop().create_future()
        await self._emit(
            Event(
                approval=ApprovalRequest(
                    name=call.name,
                    args=_args_preview(call.input),
                    reason=reason,
                    respond=respond,
                )
            )
        )
        try:
            return await respond
        except asyncio.CancelledError:
            if not respond.done():
                respond.set_result(Outcome.DENY_ONCE)
            raise

    async def _record_read_file(self, call: ToolCall, result) -> None:
        if call.name != "read_file" or getattr(result, "is_error", False):
            return
        try:
            data = json.loads(call.input or "{}")
            path = data.get("path")
            if not path:
                return
            abs_path = Path(path).resolve()
            raw = await asyncio.to_thread(abs_path.read_bytes)
        except (OSError, json.JSONDecodeError, TypeError):
            return
        self.runtime.recovery.record_file(str(abs_path), raw.decode("utf-8", errors="replace"))

    async def _emit(self, event: Event) -> None:
        """把事件发送到队列（由 run() 消费并 yield 给 TUI）。"""
        if self._event_queue is not None:
            await self._event_queue.put(event)

    async def _run_one(
        self,
        idx: int,
        call: ToolCall,
        results: list[ToolResult | None],
        cancel: asyncio.Event,
    ) -> None:
        """执行单个工具调用，结果写入 results[idx]。支持 cancel 中断。"""
        results[idx] = await self._execute_and_result(call, cancel)

    def _fill_cancelled(
        self,
        results: list[ToolResult | None],
        calls: list[ToolCall],
        start: int,
    ) -> None:
        for k in range(start, len(results)):
            if results[k] is None:
                results[k] = ToolResult(
                    tool_call_id=calls[k].id,
                    content=NOTICE_CANCELLED,
                    is_error=True,
                )

    def _finalize_results(
        self, results: list[ToolResult | None], calls: list[ToolCall]
    ) -> list[ToolResult]:
        finalized: list[ToolResult] = []
        for k, r in enumerate(results):
            if r is not None:
                finalized.append(r)
            else:
                finalized.append(
                    ToolResult(
                        tool_call_id=calls[k].id if k < len(calls) else "",
                        content="（未执行）",
                        is_error=True,
                    )
                )
        return finalized

    def _all_unknown(self, calls: list[ToolCall]) -> bool:
        for c in calls:
            if self._registry.get(c.name) is not None:
                return False
        return True

    def _ensure_final(self, text: str) -> str:
        if text.strip():
            return text
        return "（工具已执行完毕。如果你需要更多分析，请继续提问，我会基于已有结果给出详细回答。）"

    def _ensure_assistant_tail(self, conv: Conversation, fallback: str) -> None:
        if conv.last_role() != "assistant":
            conv.add_assistant(fallback)

    def _finish_cancelled(self, conv: Conversation) -> None:
        self._ensure_assistant_tail(conv, NOTICE_CANCELLED)
