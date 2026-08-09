"""ReAct 循环编排——模型自主多轮：想 → 调工具 → 看结果 → 边做边调整，直到任务完成。"""

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from novacode import prompt
from novacode.agent.context_manager import ContextManager, SessionRuntime
from novacode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    TriggerKind,
    new_session_context,
)
from novacode.compact.const import MANUAL_SAFETY_MARGIN, auto_compact_threshold
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
from novacode.permission import Mode, Outcome
from novacode.permission.engine import Engine
from novacode.tool import Registry, cwd_from_ctx

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
class _StreamState:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: LLMUsage | None = None
    err: Exception | None = None
    cancelled: bool = False


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
        self._runtime = runtime or SessionRuntime(
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
        self._run_lock = asyncio.Lock()
        self.active_skills: dict[str, str] = {}
        self._skill_catalog = ""
        self._context_manager = ContextManager(
            provider,
            self._runtime,
            context_window=context_window,
            dispatch_hook=self._dispatch_hook,
        )
        from novacode.agent.tool_runner import ToolRunner

        self._tool_runner = ToolRunner(
            registry,
            dispatch_hook=self._dispatch_hook,
            engine=engine,
            dont_ask=dont_ask,
            approval_upgrader=approval_upgrader,
            subagent_name=subagent_name,
            allowed_tools=allowed_tools,
            owner=self,
            record_read=self._context_manager.record_read_file,
        )

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

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog

    def set_allowed_tools(self, names: list[str]) -> None:
        self.allowed_tools = frozenset(names)
        self._tool_runner.set_allowed_tools(names)

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
    def runtime(self) -> SessionRuntime:
        return self._runtime

    @runtime.setter
    def runtime(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime
        if hasattr(self, "_context_manager"):
            self._context_manager.runtime = runtime

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

    async def run_force_compact(
        self,
        conv: Conversation,
        tool_defs: list[ToolDefinition],
        runtime: SessionRuntime | None = None,
        mode: Mode = Mode.DEFAULT,
    ) -> tuple[int, int]:
        async with self._run_lock:
            selected = self.runtime
            if runtime is not None:
                self.runtime = runtime
            try:
                out = await self._context_manager.prepare(
                    conv,
                    tool_defs,
                    TriggerKind.MANUAL,
                    mode,
                )
            finally:
                if runtime is not None:
                    self.runtime = selected
            return out.before_tokens, out.after_tokens

    async def run(
        self,
        conv: Conversation,
        mode: Mode,
        cancel: asyncio.Event,
    ) -> AsyncIterator[Event]:
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

            estimated = self._context_manager.estimate(conv)
            auto_candidate = (
                estimated >= auto_compact_threshold(self.context_window)
                and not self.runtime.auto_tracking.tripped()
            )
            try:
                compact_out = await self._context_manager.prepare(
                    conv,
                    defs,
                    TriggerKind.AUTO,
                    mode,
                )
            except Exception as e:
                if auto_candidate:
                    yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_AUTO))
                    yield Event(compact=CompactEvent(phase=CompactPhase.AFTER_AUTO, err=e))
                yield Event(err=e)
                persistence_err = self._persist_assistant_tail(conv, NOTICE_STREAM_ERR)
                if persistence_err is not None:
                    yield Event(err=persistence_err)
                return
            if compact_out.summarized:
                yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_AUTO))
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
                        emergency_out = await self._context_manager.prepare(
                            conv,
                            defs,
                            TriggerKind.EMERGENCY,
                            mode,
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
                    retry_estimate = self._context_manager.estimate(conv)
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
                self._context_manager.record_usage(usage, conv)
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
            results: list[ToolResult] = []
            completed = True
            try:
                async for update in self._tool_runner.run(calls, conv, cancel, mode):
                    if update.event is not None:
                        yield update.event
                    if update.result is not None:
                        results = update.result.results
                        completed = update.result.completed
                        unknown_run = unknown_run + 1 if update.result.all_unknown else 0
            except asyncio.CancelledError:
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
