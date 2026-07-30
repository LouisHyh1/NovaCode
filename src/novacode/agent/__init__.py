"""ReAct 循环编排——模型自主多轮：想 → 调工具 → 看结果 → 边做边调整，直到任务完成。"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
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
from novacode.hook import DispatchResult as HookDispatchResult
from novacode.hook import Engine as HookEngine
from novacode.hook import Event as HookEvent
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
from novacode.memory import MemoryTurn
from novacode.permission import Decision, Mode, Outcome
from novacode.permission.engine import Engine
from novacode.permission.persist import persist_local_allow
from novacode.tool import DEFAULT_TIMEOUT, Registry, cwd_from_ctx, resolve_path

logger = logging.getLogger(__name__)

MAX_ITERATIONS: int = 25
MAX_UNKNOWN_RUN: int = 3

PLAN_REMINDER_INTERVAL: int = 4

NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"
NOTICE_MEMORY_NOT_WRITTEN = "记忆未写入：manage_memory 未成功执行。"
NOTICE_MEMORY_WRITTEN = "记忆已写入：manage_memory 已成功执行。"


class MaxTurnsReached(RuntimeError):  # noqa: N818 - 文档规定的公开异常名
    """子 Agent 达到最大迭代轮数。"""

    def __init__(self, final_text: str = "") -> None:
        super().__init__(final_text or "SubAgent reached max_turns")
        self.final_text = final_text


_EXPLICIT_MEMORY_RE = re.compile(
    r"(?:请|帮我|务必|要)?记住(?:我|这|以下|：|:|\s)|"
    r"保存到(?:长期)?记忆|更新[^。！？?]{0,12}记忆|"
    r"忘记(?:我|这|关于|之前)|从(?:长期)?记忆[^。！？?]{0,12}删除|"
    r"\bremember\s+(?:that|this|my|i\b)|"
    r"\bsave\b[^.!?]{0,40}\b(?:to|in)\s+(?:long-term\s+)?memory\b|"
    r"\bupdate\b[^.!?]{0,40}\bmemory\b|"
    r"\b(?:forget|remove|delete)\b[^.!?]{0,40}\b(?:memory|that|this|my)\b",
    re.IGNORECASE,
)


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


ApprovalUpgrader = Callable[[ApprovalRequest], Awaitable[tuple[Outcome, bool]]]


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
    memory_turn: MemoryTurn | None = None


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
        """为新会话重置所有跨轮上下文状态。"""
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


def _is_explicit_memory_request(text: str) -> bool:
    if re.search(
        r"(?:如何|怎么|怎样|什么是|介绍|解释)[^。！？?]{0,20}(?:记忆|记住|忘记)|"
        r"\b(?:how|what|explain)\b[^.!?]{0,40}\b(?:memory|remember|forget)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    return _EXPLICIT_MEMORY_RE.search(text) is not None


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
        instructions: str = "",
        memory_index: Callable[[], str] | None = None,
        hook_engine: HookEngine | None = None,
        system_prompt: str | None = None,
        max_turns: int = 0,
        permission_mode: Mode | None = None,
        dont_ask: bool = False,
        approval_upgrader: ApprovalUpgrader | None = None,
        allowed_tools: list[str] | None = None,
        subagent_name: str = "",
        teammate_context=None,
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
        self.instructions = instructions
        self.memory_index = memory_index or (lambda: "")
        self._hook_engine = hook_engine
        self.runtime.hook_engine = hook_engine
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.permission_mode = permission_mode
        self.dont_ask = dont_ask
        self.approval_upgrader = approval_upgrader
        self.allowed_tools = None if allowed_tools is None else frozenset(allowed_tools)
        self.subagent_name = subagent_name
        self.teammate_context = teammate_context
        self._active_conv: Conversation | None = None
        self._run_lock = asyncio.Lock()
        self.active_skills: dict[str, str] = {}
        self._skill_catalog = ""

    def _hook_payload(self, mode: Mode, **values) -> dict:
        cwd = cwd_from_ctx() or (self.engine.root if self.engine is not None else str(Path.cwd()))
        return {
            "session_id": self.runtime.session.session_id,
            "cwd": cwd,
            "mode": str(mode),
            **values,
        }

    async def _dispatch_hook(self, event: HookEvent, mode: Mode, **values) -> HookDispatchResult:
        if self._hook_engine is None:
            return HookDispatchResult()
        result = await self._hook_engine.dispatch(event, self._hook_payload(mode, **values))
        self.runtime.append_reminders(result.injected_prompts)
        return result

    @staticmethod
    def _tool_input(call: ToolCall) -> dict:
        try:
            value = json.loads(call.input)
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog

    def set_allowed_tools(self, names: list[str]) -> None:
        self.allowed_tools = frozenset(names)

    def append_system_prompt(self, suffix: str) -> None:
        base = self.system_prompt or prompt.build_system_prompt(
            instructions=self.instructions,
            memory_index=self.memory_index(),
        )
        self.system_prompt = f"{base}\n\n{suffix}" if suffix else base

    @property
    def provider(self) -> Provider:
        return self._provider

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def version(self) -> str:
        return self._version

    @property
    def hook_engine(self) -> HookEngine | None:
        return self._hook_engine

    def _tool_definitions(self, mode: Mode) -> list[ToolDefinition]:
        definitions = (
            self._registry.read_only_definitions()
            if mode == Mode.PLAN
            else self._registry.definitions()
        )
        if self.allowed_tools is None:
            return definitions
        return [definition for definition in definitions if definition.name in self.allowed_tools]

    def _manage_input(
        self,
        conv: Conversation,
        defs: list[ToolDefinition],
        trigger: TriggerKind,
        estimated: int,
        runtime: SessionRuntime | None = None,
    ) -> ManageInput:
        selected = runtime or self.runtime
        return ManageInput(
            conv=conv,
            provider=self._provider,
            model=self._provider.model,
            context_window=self.context_window,
            tool_defs=defs,
            replacement=selected.replacement,
            recovery=selected.recovery,
            auto_tracking=selected.auto_tracking,
            session=selected.session,
            usage_anchor=selected.usage_anchor,
            anchor_msg_len=selected.anchor_msg_len,
            estimated_token=estimated,
            trigger=trigger,
        )

    async def run_force_compact(
        self,
        conv: Conversation,
        tool_defs: list[ToolDefinition],
        runtime: SessionRuntime | None = None,
        mode: Mode = Mode.DEFAULT,
    ) -> tuple[int, int]:
        async with self._run_lock:
            selected = runtime or self.runtime
            estimated = estimate_tokens(0, conv.messages(), 0)
            await self._dispatch_hook(HookEvent.PRE_COMPACT, mode, trigger="manual")
            out = await manage_context(
                self._manage_input(
                    conv,
                    tool_defs,
                    TriggerKind.MANUAL,
                    estimated,
                    selected,
                )
            )
            selected.usage_anchor = 0
            selected.anchor_msg_len = 0
            await self._dispatch_hook(
                HookEvent.POST_COMPACT,
                mode,
                trigger="manual",
                before_tokens=out.before_tokens,
                after_tokens=out.after_tokens,
            )
            return out.before_tokens, out.after_tokens

    async def run(
        self,
        conv: Conversation,
        mode: Mode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event]:
        self._active_conv = conv
        if self.permission_mode is not None:
            mode = self.permission_mode
        env = prompt.gather_environment(
            self._version,
            self._provider.model,
            cwd_from_ctx(),
        )
        sys = (
            self.system_prompt
            if self.system_prompt is not None
            else prompt.build_system_prompt(
                instructions=self.instructions,
                memory_index=self.memory_index(),
            )
        )
        defs = self._tool_definitions(mode)

        unknown_run = 0
        latest_user = next(
            (
                message.content
                for message in reversed(conv.messages())
                if message.role == "user" and message.content.strip()
            ),
            "",
        )
        explicit_memory = _is_explicit_memory_request(latest_user)
        memory_succeeded = False

        max_iterations = self.max_turns or MAX_ITERATIONS
        for it in range(1, max_iterations + 1):
            yield Event(iter=it)
            if cancel.is_set():
                persistence_err = self._persist_assistant_tail(conv, NOTICE_CANCELLED)
                if persistence_err is not None:
                    yield Event(err=persistence_err)
                return

            if self.teammate_context is not None:
                from novacode.agent.team_mailbox import ingest_team_mailbox

                await ingest_team_mailbox(self)

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
                await self._dispatch_hook(HookEvent.PRE_COMPACT, mode, trigger="auto")
                compact_out = await manage_context(
                    self._manage_input(conv, defs, TriggerKind.AUTO, estimated)
                )
                await self._dispatch_hook(
                    HookEvent.POST_COMPACT,
                    mode,
                    trigger="auto",
                    before_tokens=compact_out.before_tokens,
                    after_tokens=compact_out.after_tokens,
                )
            except Exception as e:
                if emit_auto:
                    yield Event(compact=CompactEvent(phase=CompactPhase.AFTER_AUTO, err=e))
                yield Event(err=e)
                persistence_err = self._persist_assistant_tail(conv, NOTICE_STREAM_ERR)
                if persistence_err is not None:
                    yield Event(err=persistence_err)
                return
            if emit_auto:
                yield Event(
                    compact=CompactEvent(
                        phase=CompactPhase.AFTER_AUTO,
                        before=compact_out.before_tokens,
                        after=compact_out.after_tokens,
                    )
                )

            env_text = prompt.build_environment_context(
                env.render(),
                self.active_skills,
                self._skill_catalog,
            )

            emergency_retried = False
            while True:
                await self._dispatch_hook(
                    HookEvent.PRE_USER_MESSAGE,
                    mode,
                    prompt=latest_user,
                )
                reminders = [self.runtime.resume_reminder] if self.runtime.resume_reminder else []
                if mode == Mode.PLAN:
                    full = it == 1 or (it - 1) % PLAN_REMINDER_INTERVAL == 0
                    reminders.append(prompt.plan_reminder(full))
                reminders.extend(self.runtime.take_reminders())
                reminder = "\n\n".join(reminders)
                stream_state = _StreamState()
                async for ev in self._stream_once(
                    conv,
                    defs,
                    sys,
                    env_text,
                    reminder,
                    cancel,
                    stream_state,
                    emit_text=not explicit_memory or memory_succeeded,
                ):
                    yield ev

                text = stream_state.text
                calls = stream_state.calls or []
                usage = stream_state.usage
                err = stream_state.err

                if err is None:
                    if stream_state.cancelled:
                        persistence_err = self._persist_assistant_tail(conv, NOTICE_CANCELLED)
                        if persistence_err is not None:
                            yield Event(err=persistence_err)
                        return
                    break

                if cancel.is_set():
                    persistence_err = self._persist_assistant_tail(conv, NOTICE_CANCELLED)
                    if persistence_err is not None:
                        yield Event(err=persistence_err)
                    return

                if isinstance(err, PromptTooLongError) and not emergency_retried:
                    yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_EMERGENCY))
                    try:
                        await self._dispatch_hook(HookEvent.PRE_COMPACT, mode, trigger="emergency")
                        emergency_in = self._manage_input(
                            conv,
                            defs,
                            TriggerKind.EMERGENCY,
                            estimate_tokens(0, conv.messages(), 0),
                        )
                        emergency_out = await manage_context(emergency_in)
                        await self._dispatch_hook(
                            HookEvent.POST_COMPACT,
                            mode,
                            trigger="emergency",
                            before_tokens=emergency_out.before_tokens,
                            after_tokens=emergency_out.after_tokens,
                        )
                    except Exception as e:
                        yield Event(
                            compact=CompactEvent(
                                phase=CompactPhase.AFTER_EMERGENCY,
                                err=e,
                            )
                        )
                        yield Event(err=e)
                        persistence_err = self._persist_assistant_tail(conv, NOTICE_STREAM_ERR)
                        if persistence_err is not None:
                            yield Event(err=persistence_err)
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

                await self._dispatch_hook(
                    HookEvent.NOTIFICATION,
                    mode,
                    kind="stream_error",
                    detail=str(err),
                )
                yield Event(err=err)
                persistence_err = self._persist_assistant_tail(conv, NOTICE_STREAM_ERR)
                if persistence_err is not None:
                    yield Event(err=persistence_err)
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
                if explicit_memory and not memory_succeeded:
                    final = NOTICE_MEMORY_NOT_WRITTEN
                elif explicit_memory and memory_succeeded and not text.strip():
                    final = NOTICE_MEMORY_WRITTEN
                else:
                    final = self._ensure_final(text)
                try:
                    conv.add_assistant(final)
                except Exception as exc:
                    yield Event(err=exc)
                    return
                if explicit_memory and (not memory_succeeded or not text.strip()):
                    yield Event(text=final)
                await self._dispatch_hook(HookEvent.STOP, mode, iter=it)
                yield Event(done=True, memory_turn=self._memory_turn(conv, final))
                return

            try:
                conv.add_assistant_with_tool_calls(text, calls)
            except Exception as exc:
                yield Event(err=exc)
                return
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
                    self._event_queue = None
                    conv.add_tool_results(
                        [
                            ToolResult(
                                tool_call_id=call.id,
                                content=NOTICE_CANCELLED,
                                is_error=True,
                            )
                            for call in calls
                        ]
                    )
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

            try:
                conv.add_tool_results(results)
            except Exception as exc:
                yield Event(err=exc)
                return

            memory_results = [
                result
                for call, result in zip(calls, results, strict=True)
                if call.name == "manage_memory"
            ]
            if memory_results:
                memory_succeeded = all(not result.is_error for result in memory_results)
                if explicit_memory and not memory_succeeded:
                    try:
                        conv.add_assistant(NOTICE_MEMORY_NOT_WRITTEN)
                    except Exception as exc:
                        yield Event(err=exc)
                        return
                    await self._dispatch_hook(HookEvent.STOP, mode, iter=it)
                    yield Event(
                        text=NOTICE_MEMORY_NOT_WRITTEN,
                        done=True,
                        memory_turn=self._memory_turn(conv, NOTICE_MEMORY_NOT_WRITTEN),
                    )
                    return

            # Plan 模式硬拒绝：输出确定性收尾，不让模型自由总结误报成功
            if mode == Mode.PLAN and any(getattr(r, "is_policy_denial", False) for r in results):
                terminal_text = (
                    "计划模式已拒绝执行写入/命令操作。"
                    "未对文件系统做任何修改。"
                    "如需执行，请使用 /do 或切换到 ACCEPT_EDITS/BYPASS 模式。"
                )
                try:
                    conv.add_assistant(terminal_text)
                except Exception as exc:
                    yield Event(err=exc)
                    return
                await self._dispatch_hook(HookEvent.STOP, mode, iter=it)
                yield Event(
                    text=terminal_text,
                    done=True,
                    memory_turn=self._memory_turn(conv, terminal_text),
                )
                return

            if not completed:
                persistence_err = self._persist_assistant_tail(conv, NOTICE_CANCELLED)
                if persistence_err is not None:
                    yield Event(err=persistence_err)
                return

            if unknown_run >= MAX_UNKNOWN_RUN:
                notice = (
                    NOTICE_MEMORY_NOT_WRITTEN
                    if explicit_memory and not memory_succeeded
                    else NOTICE_UNKNOWN_TOOLS
                )
                yield Event(notice=notice)
                persistence_err = self._persist_assistant_tail(conv, notice)
                if persistence_err is not None:
                    yield Event(err=persistence_err)
                    return
                await self._dispatch_hook(HookEvent.STOP, mode, iter=it)
                yield Event(
                    done=True,
                    memory_turn=self._memory_turn(conv, notice),
                )
                return

        max_notice = (
            NOTICE_MAX_ITER
            if max_iterations == MAX_ITERATIONS
            else f"（已达最大迭代轮数 {max_iterations}，自动停止。）"
        )
        notice = (
            NOTICE_MEMORY_NOT_WRITTEN if explicit_memory and not memory_succeeded else max_notice
        )
        yield Event(notice=notice)
        persistence_err = self._persist_assistant_tail(conv, notice)
        if persistence_err is not None:
            yield Event(err=persistence_err)
            return
        await self._dispatch_hook(HookEvent.STOP, mode, iter=max_iterations)
        yield Event(done=True, memory_turn=self._memory_turn(conv, notice))

    async def run_to_completion(
        self,
        conv: Conversation,
        task: str,
        events: asyncio.Queue | None = None,
    ) -> str:
        """复用主 ReAct 循环运行到自然结束，并返回末条 assistant 文本。"""
        if task:
            conv.add_user(task)
        reached_limit = False
        async for event in self.run(
            conv,
            self.permission_mode or Mode.DEFAULT,
            asyncio.Event(),
        ):
            if events is not None:
                await events.put(event)
            if event.err is not None:
                raise event.err
            if event.notice.startswith("（已达最大迭代轮数"):
                reached_limit = True
            if event.done:
                break
        final_text = next(
            (
                message.content
                for message in reversed(conv.messages())
                if message.role == "assistant" and message.content
            ),
            "",
        )
        if reached_limit:
            raise MaxTurnsReached(final_text)
        return final_text

    async def _stream_once(
        self,
        conv: Conversation,
        defs: list,
        sys: str,
        env_text: str,
        reminder: str,
        cancel: asyncio.Event,
        state: _StreamState,
        *,
        emit_text: bool = True,
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
                    if emit_text:
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
                    await self._emit(
                        Event(
                            tool=ToolEvent(
                                name=calls[k].name,
                                args=_args_preview(calls[k].input),
                                phase=Phase.START,
                            )
                        )
                    )
                    blocked = await self._pre_tool_hook(calls[k], mode)
                    if blocked is not None:
                        results[k] = blocked
                        done[k - i] = True
                    elif self.engine is not None:
                        decision, reason = self.engine.check(mode, calls[k], True)
                        if decision == Decision.DENY:
                            results[k] = ToolResult(
                                tool_call_id=calls[k].id, content=reason, is_error=True
                            )
                            done[k - i] = True

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
                        await self._post_tool_hook(calls[k], r, mode)
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
                blocked = await self._pre_tool_hook(calls[i], mode)
                if blocked is None:
                    r, ok = await self._run_side_effect(calls[i], cancel, mode)
                else:
                    r, ok = blocked, True
                await self._post_tool_hook(calls[i], r, mode)
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

    async def _pre_tool_hook(self, call: ToolCall, mode: Mode) -> ToolResult | None:
        outcome = await self._dispatch_hook(
            HookEvent.PRE_TOOL_USE,
            mode,
            tool_name=call.name,
            tool_input=self._tool_input(call),
        )
        if not outcome.blocked:
            return None
        return ToolResult(
            tool_call_id=call.id,
            content=f"[hook {outcome.blocking_hook_name}] {outcome.reason}",
            is_error=True,
        )

    async def _post_tool_hook(self, call: ToolCall, result: ToolResult, mode: Mode) -> None:
        await self._dispatch_hook(
            HookEvent.POST_TOOL_USE,
            mode,
            tool_name=call.name,
            tool_input=self._tool_input(call),
            tool_result=result.content,
            is_error=result.is_error,
        )

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
        if self.dont_ask:
            return await self._execute_allowed(call, cancel)
        try:
            outcome = await self._request_approval(call, reason, mode)
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

        if self.allowed_tools is not None and call.name not in self.allowed_tools:
            return ToolResult(
                tool_call_id=call.id,
                content=f"工具 {call.name} 对当前 SubAgent 不可用",
                is_error=True,
            )

        from novacode.agent.context import ExecutionContext, bind, reset

        context_token = bind(ExecutionContext(self, self._active_conv or Conversation()))
        try:
            tool = self._registry.get(call.name)
            timeout = getattr(tool, "timeout", DEFAULT_TIMEOUT)
            exec_task = asyncio.create_task(
                self._registry.execute(call.name, call.input, timeout=timeout)
            )
        finally:
            reset(context_token)
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

    async def _request_approval(self, call: ToolCall, reason: str, mode: Mode) -> Outcome:
        """发出人在回路请求事件，await Future 等待 TUI 回传用户选择。"""
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
                f"[来自 SubAgent {self.subagent_name}] {reason}" if self.subagent_name else reason
            ),
            respond=respond,
        )
        if self.approval_upgrader is not None:
            outcome, handled = await self.approval_upgrader(request)
            if handled:
                return outcome
        await self._emit(Event(approval=request))
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
            abs_path = Path(resolve_path(path))
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

    @staticmethod
    def _memory_turn(conv: Conversation, assistant_content: str) -> MemoryTurn | None:
        user_content = next(
            (
                message.content
                for message in reversed(conv.messages())
                if message.role == "user" and message.content.strip()
            ),
            "",
        )
        if not user_content or not assistant_content.strip():
            return None
        return MemoryTurn(user_content=user_content, assistant_content=assistant_content)

    def _ensure_assistant_tail(self, conv: Conversation, fallback: str) -> None:
        if conv.last_role() != "assistant":
            conv.add_assistant(fallback)

    def _persist_assistant_tail(self, conv: Conversation, fallback: str) -> Exception | None:
        try:
            self._ensure_assistant_tail(conv, fallback)
        except Exception as exc:
            return exc
        return None
