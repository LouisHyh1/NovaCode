"""单个活动 Session 的创建、恢复与持久化事务。"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from novacode.conversation import Conversation
from novacode.hook import Event as HookEvent
from novacode.llm import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER, Message
from novacode.session.reader import load_session, validate_compression_history
from novacode.session.types import SessionInfo, SessionWriteError
from novacode.session.writer import SessionWriter

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from novacode.agent import Agent
    from novacode.agent.context_manager import SessionRuntime
    from novacode.compact import SessionContext
    from novacode.llm import ToolDefinition


@dataclass(slots=True)
class _Write:
    writer: SessionWriter
    replacement: list[Message] | None = None
    message: Message | None = None


class SessionService:
    """隐藏 Writer、写入队列和切换准备步骤的 Session 模块。"""

    def __init__(
        self,
        project_root: Path,
        context: SessionContext | None,
        writer: SessionWriter | None,
    ) -> None:
        self._project_root = Path(project_root).resolve()
        self._context = context
        self._writer = writer
        self._model = ""
        self._closed = False
        self._write_error: SessionWriteError | None = None
        self._queue: asyncio.Queue[_Write | None] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._switch_lock = asyncio.Lock()
        self._agent: Agent | None = None
        self._tool_definitions: Callable[[], list[ToolDefinition]] = lambda: []
        self._dispatch_hook: Callable[[HookEvent], Awaitable[None]] | None = None
        self._started = False
        self._ended = False
        self._conversation = self._new_conversation()

    @classmethod
    def create(cls, project_root: Path, model: str = "") -> SessionService:
        from novacode.compact import new_session_context

        root = Path(project_root).resolve()
        context = new_session_context(str(root))
        writer = SessionWriter(Path(context.message_path).parent, context.session_id, model)
        service = cls(root, context, writer)
        service._model = model
        return service

    @classmethod
    def ephemeral(cls, project_root: Path | None = None) -> SessionService:
        return cls(Path(project_root or Path.cwd()), None, None)

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    @property
    def context(self) -> SessionContext | None:
        return self._context

    @property
    def session_id(self) -> str:
        return self._context.session_id if self._context is not None else ""

    @property
    def path(self) -> Path:
        if self._writer is None:
            raise SessionWriteError("session persistence is disabled")
        return self._writer.path

    def bind_model(self, model: str) -> None:
        self._ensure_open()
        if self._writer is not None:
            self._writer.bind_model(model)
        self._model = model

    def bind_agent(
        self,
        agent: Agent,
        tool_definitions: Callable[[], list[ToolDefinition]],
    ) -> None:
        self._ensure_open()
        self._agent = agent
        self._tool_definitions = tool_definitions
        if self._context is not None:
            agent.runtime = self._new_runtime(self._context)

    def bind_lifecycle(
        self,
        dispatch_hook: Callable[[HookEvent], Awaitable[None]],
    ) -> None:
        self._dispatch_hook = dispatch_hook

    async def start(self) -> None:
        if self._agent is None or (self._started and not self._ended):
            return
        await self._dispatch(HookEvent.SESSION_START)
        self._started = True
        self._ended = False

    async def end(self) -> None:
        if not self._started or self._ended:
            return
        await self._dispatch(HookEvent.SESSION_END)
        self._ended = True

    async def record(self, message: Message) -> None:
        self._ensure_open()
        self._append(message)
        await self.sync()

    async def sync(self) -> None:
        queue = self._queue
        if queue is not None:
            await queue.join()
        if self._write_error is not None:
            raise self._write_error

    async def new_session(self) -> None:
        from novacode.compact import new_session_context

        self._ensure_persistent()
        context = new_session_context(str(self._project_root))
        writer = SessionWriter(Path(context.message_path).parent, context.session_id, self._model)
        conversation = self._new_conversation()
        await self._commit_switch(context, writer, conversation, resumed=False)

    async def resume(self, info: SessionInfo) -> None:
        from novacode.compact import SessionContext, open_session_context

        self._ensure_persistent()
        staging_root: Path | None = None
        promoted: list[Path] = []
        writer: SessionWriter | None = None
        history_committed = False
        try:
            loaded = await asyncio.to_thread(load_session, info.path)
            if not loaded.messages or not loaded.model:
                raise ValueError("会话没有可恢复的有效消息")
            validate_compression_history(
                loaded, context_compression=self._agent is None or self._agent.context_compression
            )
            context = open_session_context(str(self._project_root), info.session_id)
            candidate = Conversation.from_messages(loaded.messages)
            reminder = self._build_resume_reminder(loaded.last_activity)
            compacted = self._needs_resume_compaction(candidate)

            if compacted:
                staging_root = info.path.parent / f".resume-staging-{uuid.uuid4().hex}"
                staging_spill = staging_root / "tool-results"
                staging_spill.mkdir(parents=True)
                staging_context = SessionContext(
                    session_id=staging_root.name,
                    message_path=str(staging_root / "messages.jsonl"),
                    spill_dir=str(staging_spill),
                )
                await self._compact_resume_candidate(candidate, staging_context)
                promoted = await asyncio.to_thread(
                    self._promote_staged_spills,
                    candidate,
                    staging_spill,
                    Path(context.spill_dir),
                )

            writer = await asyncio.to_thread(
                SessionWriter.open_existing,
                info.path.parent,
                info.session_id,
                loaded.model,
            )
            if compacted:
                await asyncio.to_thread(writer.append_compaction, candidate.messages())
                history_committed = True
            conversation = Conversation.from_messages(
                candidate.messages(),
                self._enqueue_message,
                self._enqueue_compaction,
            )
            await self._commit_switch(
                context,
                writer,
                conversation,
                resumed=True,
                resume_reminder=reminder,
            )
        except BaseException:
            if writer is not None and writer is not self._writer:
                await asyncio.to_thread(writer.close)
            if promoted and not history_committed:
                self._remove_promoted(promoted)
            raise
        finally:
            if staging_root is not None:
                await asyncio.to_thread(shutil.rmtree, staging_root, True)
        self._model = loaded.model

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        error: BaseException | None = None
        try:
            await self.sync()
        except BaseException as exc:
            error = exc
        queue = self._queue
        worker = self._worker
        if queue is not None and worker is not None:
            queue.put_nowait(None)
            await worker
        if self._writer is not None:
            try:
                await asyncio.to_thread(self._writer.close)
            except BaseException as exc:
                error = error or exc
        if error is not None:
            raise error

    async def _commit_switch(
        self,
        context: SessionContext,
        writer: SessionWriter,
        conversation: Conversation,
        *,
        resumed: bool,
        resume_reminder: str = "",
    ) -> None:
        async with self._switch_lock:
            await self.sync()
            old_writer = self._writer
            agent = self._agent
            await self.end()
            if agent is not None:
                await agent.runtime.reset_hooks_for_new_session()
                agent.clear_active_skills()
            self._context = context
            self._writer = writer
            self._conversation = conversation
            if agent is not None:
                agent.runtime = self._new_runtime(context, resume_reminder)
        if old_writer is not None:
            try:
                await asyncio.to_thread(old_writer.close)
            except Exception as exc:
                logger.warning("old session writer close failed: %s", type(exc).__name__)
        try:
            if resumed:
                await self._dispatch(HookEvent.SESSION_RESUME)
                self._started = True
                self._ended = False
            else:
                await self.start()
        except Exception as exc:
            logger.warning("session lifecycle hook failed: %s", type(exc).__name__)

    async def _dispatch(self, event: HookEvent) -> None:
        if self._dispatch_hook is not None:
            await self._dispatch_hook(event)

    def _new_runtime(
        self,
        context: SessionContext,
        resume_reminder: str = "",
    ) -> SessionRuntime:
        from novacode.agent.context_manager import SessionRuntime
        from novacode.compact import (
            CompactCircuitBreaker,
            ContentReplacementState,
            RecoveryState,
        )

        runtime = SessionRuntime(
            replacement=ContentReplacementState(),
            recovery=RecoveryState(),
            auto_tracking=CompactCircuitBreaker(),
            session=context,
            resume_reminder=resume_reminder,
        )
        if self._agent is not None:
            runtime.hook_engine = self._agent.hook_engine
        return runtime

    def _needs_resume_compaction(self, candidate: Conversation) -> bool:
        from novacode.compact.const import auto_compact_threshold
        from novacode.compact.token import estimate_tokens

        agent = self._agent
        if agent is None or not agent.context_compression:
            return False
        estimated = estimate_tokens(0, candidate.messages(), 0)
        return estimated > auto_compact_threshold(agent.context_window)

    async def _compact_resume_candidate(
        self,
        candidate: Conversation,
        staging_context: SessionContext,
    ) -> None:
        agent = self._agent
        if agent is None:
            raise RuntimeError("no active agent")
        await agent.run_force_compact(
            candidate,
            self._tool_definitions(),
            runtime=self._new_runtime(staging_context),
        )

    @staticmethod
    def _promote_staged_spills(
        candidate: Conversation,
        staging_spill_dir: Path,
        target_spill_dir: Path,
    ) -> list[Path]:
        target_spill_dir.mkdir(parents=True, exist_ok=True)
        promoted: list[Path] = []
        path_map: dict[str, str] = {}
        try:
            for source in sorted(staging_spill_dir.rglob("*")):
                if not source.is_file():
                    continue
                target = SessionService._reserve_spill_path(target_spill_dir, source.name)
                try:
                    shutil.copyfile(source, target)
                    source.unlink()
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                promoted.append(target)
                path_map[str(source)] = str(target)

            messages = candidate.messages()
            for message in messages:
                for result in message.tool_results:
                    for source_text, target_text in path_map.items():
                        result.content = result.content.replace(source_text, target_text)
            candidate.replace_history(messages)
            return promoted
        except Exception:
            SessionService._remove_promoted(promoted)
            raise

    @staticmethod
    def _reserve_spill_path(directory: Path, filename: str) -> Path:
        original = Path(filename)
        counter = 0
        while True:
            suffix = "" if counter == 0 else f"-{counter}"
            candidate = directory / f"{original.stem}{suffix}{original.suffix}"
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                counter += 1
                continue
            os.close(descriptor)
            return candidate

    @staticmethod
    def _remove_promoted(paths: list[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("promoted spill cleanup failed: %s", path)

    @staticmethod
    def _build_resume_reminder(
        last_activity: datetime | None,
        now: datetime | None = None,
    ) -> str:
        from novacode.prompt import system_reminder

        if last_activity is None:
            return ""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if current - last_activity.astimezone(UTC) <= timedelta(hours=24):
            return ""
        return system_reminder(
            "该恢复会话的历史信息可能已经过期。继续前请重新读取会变化的文件、配置和外部资料。"
        )

    def _new_conversation(self) -> Conversation:
        if self._writer is None:
            return Conversation()
        return Conversation(self._enqueue_message, self._enqueue_compaction)

    def _append(self, message: Message) -> None:
        if message.role == ROLE_USER:
            self._conversation.add_user(message.content)
        elif message.role == ROLE_ASSISTANT and message.tool_calls:
            self._conversation.add_assistant_with_tool_calls(message.content, message.tool_calls)
        elif message.role == ROLE_ASSISTANT:
            self._conversation.add_assistant(message.content)
        elif message.role == ROLE_TOOL:
            self._conversation.add_tool_results(message.tool_results)
        else:
            raise ValueError(f"unsupported message role: {message.role}")

    def _enqueue_message(self, message: Message) -> None:
        self._enqueue(_Write(self._require_writer(), message=copy.deepcopy(message)))

    def _enqueue_compaction(self, replacement: list[Message]) -> None:
        self._enqueue(_Write(self._require_writer(), replacement=copy.deepcopy(replacement)))

    def _enqueue(self, write: _Write) -> None:
        self._ensure_open()
        if self._write_error is not None:
            raise self._write_error
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._write_sync(write)
            return
        if self._queue is None:
            self._queue = asyncio.Queue()
            self._worker = asyncio.create_task(self._run_writer())
        self._queue.put_nowait(write)

    async def _run_writer(self) -> None:
        assert self._queue is not None
        while True:
            write = await self._queue.get()
            try:
                if write is None:
                    return
                if self._write_error is None:
                    await asyncio.to_thread(self._write_sync, write)
            except Exception as exc:
                self._write_error = (
                    exc
                    if isinstance(exc, SessionWriteError)
                    else SessionWriteError(f"persist session failed: {exc}")
                )
            finally:
                self._queue.task_done()

    @staticmethod
    def _write_sync(write: _Write) -> None:
        if write.message is not None:
            write.writer.append_message(write.message)
        else:
            assert write.replacement is not None
            write.writer.append_compaction(write.replacement)

    def _require_writer(self) -> SessionWriter:
        if self._writer is None:
            raise SessionWriteError("session persistence is disabled")
        return self._writer

    def _ensure_persistent(self) -> None:
        self._ensure_open()
        self._require_writer()
        if not self._model:
            raise SessionWriteError("session writer model is not bound")

    def _ensure_open(self) -> None:
        if self._closed:
            raise SessionWriteError("session service is closed")
