"""Hook 事件分派与会话内状态。"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

from novacode.hook.event import Event, is_blocking
from novacode.hook.executor import ExecutionResult, Executor
from novacode.hook.matcher import eval_condition
from novacode.hook.rule import Payload, Rule


@dataclass
class DispatchResult:
    blocked: bool = False
    reason: str = ""
    blocking_hook_name: str = ""
    injected_prompts: list[str] = field(default_factory=list)


class Engine:
    def __init__(
        self,
        rules: list[Rule],
        sources: list[str],
        executor: Executor | None = None,
    ) -> None:
        self._rules = rules
        self._sources = sources
        self._once_fired: set[str] = set()
        self._lock = asyncio.Lock()
        self._executor = executor or Executor()
        self._tasks: set[asyncio.Task[None]] = set()

    async def dispatch(self, event: Event, payload: Payload) -> DispatchResult:
        result = DispatchResult()
        event_payload = {**payload, "event": event.value}
        for rule in self._rules:
            if rule.event is not event or not eval_condition(rule.condition, event_payload):
                continue
            async with self._lock:
                if rule.only_once and rule.name in self._once_fired:
                    continue
                if rule.only_once:
                    self._once_fired.add(rule.name)
            if rule.asyncio_mode:
                task = asyncio.create_task(self._run_background(rule, event_payload))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                continue
            outcome = await self._executor.run(rule, event_payload, blocking=is_blocking(event))
            if outcome.err is not None:
                self._log_failure(rule, outcome)
                continue
            if outcome.prompt:
                result.injected_prompts.append(outcome.prompt)
            if outcome.blocked and is_blocking(event):
                result.blocked = True
                result.reason = outcome.reason
                result.blocking_hook_name = rule.name
                break
        return result

    async def _run_background(self, rule: Rule, payload: Payload) -> None:
        try:
            outcome = await self._executor.run(rule, payload, blocking=False)
            if outcome.err is not None:
                self._log_failure(rule, outcome)
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _log_failure(rule: Rule, outcome: ExecutionResult) -> None:
        print(
            f"[hook {rule.name}] {rule.event.value} failed: {outcome.err}",
            file=sys.stderr,
        )

    async def reset_for_new_session(self) -> None:
        async with self._lock:
            self._once_fired.clear()

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._executor.close()

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    @property
    def sources(self) -> list[str]:
        return list(self._sources)
