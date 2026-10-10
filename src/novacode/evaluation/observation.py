"""装配端的 Provider 包装；核心仅接收普通 Provider 和工具回调。"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from novacode.agent import ToolEvent
from novacode.evaluation.budget import BudgetExceededError, RunBudget
from novacode.evaluation.contracts import require
from novacode.evaluation.ledger import Ledger, sha256
from novacode.evaluation.usage import UsageRule, normalize
from novacode.llm import Provider, Request, StreamEvent, Usage


@dataclass
class _Observation:
    usage: Usage | None = None
    ambiguous_usage: bool = False
    status: str = "incomplete"
    error_type: str = ""

    def update(self, event: StreamEvent) -> None:
        if event.usage is not None:
            if self.usage is not None and self.usage != event.usage:
                self.ambiguous_usage = True
            self.usage = event.usage
        if event.err is not None:
            self.status, self.error_type = "error", type(event.err).__name__
        elif event.done and self.status != "error":
            self.status = "completed"


class ObservedProvider:
    def __init__(
        self,
        provider: Provider,
        ledger: Ledger,
        budget: RunBudget,
        *,
        usage_rule: UsageRule | None = None,
    ) -> None:
        self._provider = provider
        self.ledger = ledger
        self.budget = budget
        self.usage_rule = usage_rule
        self._previous: dict[str, str] = {}
        self._close_task: asyncio.Task[None] | None = None

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def model(self) -> str:
        return self._provider.model

    def borrow(self) -> BorrowedProvider:
        return BorrowedProvider(self)

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        status = "completed"
        try:
            await self._provider.close()
        except BaseException:
            status = "failed"
            raise
        finally:
            self.ledger.append("provider_close", status=status)

    def _metadata(self, req: Request) -> dict[str, Any]:
        require(self._close_task is None, "Provider 已关闭或正在关闭")
        metadata_fn = getattr(self._provider, "request_metadata", None)
        if not callable(metadata_fn):
            raise ValueError("缺少可核验的 SDK 请求参数")
        metadata: dict[str, Any] = metadata_fn(req)
        retries = metadata.get("sdk_max_retries")
        require(type(retries) is int and retries == 0, "评测必须显式禁用 SDK 自动重试")
        timeout = metadata.get("timeout_seconds")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise ValueError("缺少有限 SDK 超时")
        require(math.isfinite(timeout) and timeout > 0, "缺少有限 SDK 超时")
        output = metadata.get("max_output_tokens")
        require(type(output) is int and output > 0, "缺少请求输出上限")
        return metadata

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        metadata = self._metadata(req)
        payload = asdict(req)
        # 估计只用于准入：包含正文、系统块、reminder 和完整工具 Schema。
        estimate = math.ceil(len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) / 3.5)
        try:
            reserve = self.budget.reserve_request(estimate, metadata["max_output_tokens"])
        except BudgetExceededError as exc:
            self.ledger.append("budget_stop", reason=str(exc), role=req.role)
            raise
        request_id = uuid4().hex
        group = req.logical_call_id or request_id
        retry_of = self._previous.get(group) if req.attempt > 1 else None
        self._previous[group] = request_id
        start = time.monotonic()
        self.ledger.append(
            "request_start",
            request_id=request_id,
            role=req.role,
            logical_call_id=group,
            attempt=req.attempt,
            retry_of=retry_of,
            provider=self.name,
            model=self.model,
            parameters=metadata,
            input_sha256=sha256(payload),
            input=self.ledger.artifact(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            schema_sha256=sha256([asdict(t) for t in req.tools]),
            visible_tools=[tool.name for tool in req.tools],
            input_estimate=estimate,
            estimate_kind="utf8-bytes-div-3.5",
            reserved_tokens=reserve,
        )
        observation = _Observation()
        stream: AsyncIterator[StreamEvent] | None = None
        try:
            stream = self._provider.stream(req)
            while True:
                try:
                    event = await asyncio.wait_for(
                        anext(stream), timeout=self.budget.remaining_seconds
                    )
                except StopAsyncIteration:
                    break
                observation.update(event)
                if event.usage is not None:
                    self.ledger.append(
                        "request_usage",
                        request_id=request_id,
                        usage=self._normalized(observation),
                    )
                yield event
        except asyncio.CancelledError:
            observation.status, observation.error_type = "cancelled", "CancelledError"
            raise
        except GeneratorExit:
            if observation.status == "incomplete":
                observation.status = "consumer-closed"
            raise
        except BaseException as exc:
            observation.status, observation.error_type = "error", type(exc).__name__
            raise
        finally:
            try:
                await self._close_stream(stream, observation)
            finally:
                normalized = self._normalized(observation)
                self.budget.settle_request(reserve, normalized)
                self.ledger.append(
                    "request_end",
                    request_id=request_id,
                    status=observation.status,
                    error_type=observation.error_type,
                    seconds=time.monotonic() - start,
                    usage=normalized,
                    budget=self.budget.snapshot(),
                )

    def _normalized(self, observation: _Observation) -> dict[str, Any]:
        normalized = normalize(observation.usage, self.usage_rule)
        if observation.ambiguous_usage:
            normalized["measured_total"] = None
            normalized["completeness"] = "ambiguous"
        return normalized

    async def _close_stream(
        self, stream: AsyncIterator[StreamEvent] | None, observation: _Observation
    ) -> None:
        close = getattr(stream, "aclose", None)
        if close is None:
            return
        try:
            await close()
        except Exception as exc:
            self.ledger.append("stream_close_error", error_type=type(exc).__name__)
            if observation.status in ("completed", "incomplete"):
                observation.status, observation.error_type = "error", type(exc).__name__
                raise
            # 原流错误或取消优先，关闭错误另有账本证据。

    def tool_observer(self, event: ToolEvent) -> None:
        if event.phase.value == "start":
            try:
                self.budget.start_tool()
            except BudgetExceededError as exc:
                self.ledger.append("budget_stop", reason=str(exc), call_id=event.call_id)
                raise
        data = asdict(event)
        data.pop("phase")
        data.pop("args")  # UI 预览不能冒充完整参数。
        data["authorization_status"] = data.pop("authorization")
        result = data.pop("result")
        if event.phase.value == "end":
            data["output"] = self.ledger.artifact(result)
        self.ledger.append("tool_" + event.phase.value, **data)


class BorrowedProvider:
    """借用者 close 不关闭共享资源；所有者负责一次实际 close。"""

    def __init__(self, owner: ObservedProvider) -> None:
        self._owner = owner

    @property
    def name(self) -> str:
        return self._owner.name

    @property
    def model(self) -> str:
        return self._owner.model

    def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        return self._owner.stream(req)

    async def close(self) -> None:
        return None
