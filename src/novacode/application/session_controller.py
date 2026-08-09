"""单个活动会话的显式应用层生命周期。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from novacode.runtime.errors import ConflictError, ValidationError
from novacode.runtime.reports import (
    OperationReport,
    ResourceResult,
    ResourceStatus,
)
from novacode.runtime.turn import TurnEngine, TurnEvent, TurnRequest


class AsyncResource(Protocol):
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PreparedSession:
    """尚未发布、可以安全替换当前会话的候选资源。"""

    session_id: str
    conversation: object
    engine: TurnEngine
    writer: AsyncResource
    approval: AsyncResource
    cancel: asyncio.Event


class SessionPreparer(Protocol):
    async def __call__(self, session_id: str) -> PreparedSession: ...


@dataclass(frozen=True, slots=True)
class SessionDependencies:
    prepare: SessionPreparer


class SessionController:
    def __init__(self, dependencies: SessionDependencies) -> None:
        self._dependencies = dependencies
        self._active: PreparedSession | None = None
        self._lock = asyncio.Lock()
        self._turn_active = False
        self._closed = False
        self._close_report: OperationReport | None = None
        self._close_task: asyncio.Task[OperationReport] | None = None

    @property
    def active_session_id(self) -> str:
        return self._active.session_id if self._active is not None else ""

    async def start(self, session_id: str) -> None:
        candidate = await self._prepare(session_id)
        async with self._lock:
            if self._closed:
                await _close_session(candidate, "discard_closed_candidate")
                raise ConflictError("Session Controller 已关闭")
            if self._active is not None:
                await _close_session(candidate, "discard_duplicate_start")
                raise ConflictError("Session Controller 已启动")
            self._active = candidate

    async def submit(self, user_input: str, *, mode: int = 0) -> AsyncIterator[TurnEvent]:
        async with self._lock:
            active = self._require_active()
            if self._turn_active:
                raise ConflictError("当前会话已有执行中的 turn")
            self._turn_active = True
            active.cancel.clear()
        try:
            request = TurnRequest(
                conversation=active.conversation,
                user_input=user_input,
                mode=mode,
                cancel=active.cancel,
            )
            async for event in active.engine.run(request):
                yield event
        finally:
            async with self._lock:
                self._turn_active = False

    async def switch_session(self, session_id: str) -> OperationReport:
        candidate = await self._prepare(session_id)
        async with self._lock:
            if self._closed:
                await _close_session(candidate, "discard_closed_candidate")
                raise ConflictError("Session Controller 已关闭")
            if self._turn_active:
                await _close_session(candidate, "discard_busy_candidate")
                raise ConflictError("执行中的 turn 不能切换会话")
            previous = self._require_active()
            self._active = candidate
        return await _close_session(previous, "switch_session")

    def cancel(self) -> None:
        active = self._require_active()
        active.cancel.set()

    async def close(self) -> OperationReport:
        async with self._lock:
            if self._close_report is not None:
                return self._close_report
            if self._close_task is None:
                self._closed = True
                active = self._active
                self._active = None
                self._close_task = asyncio.create_task(_close_active(active))
            close_task = self._close_task
        report = await asyncio.shield(close_task)
        async with self._lock:
            if self._close_report is None:
                self._close_report = report
            return self._close_report

    async def _prepare(self, session_id: str) -> PreparedSession:
        if not session_id:
            raise ValidationError("session_id 不能为空")
        candidate = await self._dependencies.prepare(session_id)
        if candidate.session_id != session_id:
            await _close_session(candidate, "discard_invalid_candidate")
            raise ValidationError("候选会话 ID 与请求不一致")
        return candidate

    def _require_active(self) -> PreparedSession:
        if self._closed:
            raise ConflictError("Session Controller 已关闭")
        if self._active is None:
            raise ConflictError("Session Controller 尚未启动")
        return self._active


async def _close_session(session: PreparedSession, operation: str) -> OperationReport:
    results = []
    for name, resource in (("writer", session.writer), ("approval", session.approval)):
        try:
            await resource.close()
        except Exception as exc:  # noqa: BLE001 - 清理必须继续收集独立资源的结果
            results.append(
                ResourceResult(
                    resource=f"{session.session_id}:{name}",
                    status=ResourceStatus.FAILED,
                    error_category=type(exc).__name__,
                    message=str(exc),
                )
            )
        else:
            results.append(
                ResourceResult(
                    resource=f"{session.session_id}:{name}",
                    status=ResourceStatus.SUCCEEDED,
                )
            )
    return OperationReport(operation, tuple(results))


async def _close_active(session: PreparedSession | None) -> OperationReport:
    if session is None:
        return OperationReport("close_session")
    session.cancel.set()
    return await _close_session(session, "close_session")
