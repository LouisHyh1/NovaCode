"""Hook 动作执行器。"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass

import httpx

from novacode.hook.rule import (
    ActionType,
    HttpAction,
    Payload,
    Rule,
    ShellAction,
    SubagentAction,
)


@dataclass
class ExecutionResult:
    blocked: bool = False
    reason: str = ""
    prompt: str = ""
    err: Exception | None = None


class Executor:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def run(self, rule: Rule, payload: Payload, *, blocking: bool) -> ExecutionResult:
        try:
            if rule.action.type is ActionType.SHELL:
                return await self._run_shell(rule.action, payload, blocking, rule.timeout_s)
            if rule.action.type is ActionType.PROMPT:
                return ExecutionResult(prompt=rule.action.text)
            if rule.action.type is ActionType.HTTP:
                return await self._run_http(rule.action, payload, blocking, rule.timeout_s)
            self._run_subagent(rule.action, rule.name)
            return ExecutionResult()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ExecutionResult(err=exc)

    async def _run_shell(
        self,
        action: ShellAction,
        payload: Payload,
        blocking: bool,
        timeout_s: float,
    ) -> ExecutionResult:
        process = await asyncio.create_subprocess_shell(
            action.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload, sort_keys=True).encode()),
                timeout=timeout_s,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return ExecutionResult(err=TimeoutError(f"timed out after {timeout_s:g}s"))
        out = stdout.decode(errors="replace").rstrip("\r\n")
        error = stderr.decode(errors="replace").rstrip("\r\n")
        if blocking and process.returncode == 2:
            return ExecutionResult(blocked=True, reason=error or out)
        if process.returncode != 0:
            return ExecutionResult(err=RuntimeError(f"exit {process.returncode}: {error or out}"))
        if error:
            print(error, file=sys.stderr)
        return ExecutionResult()

    async def _run_http(
        self,
        action: HttpAction,
        payload: Payload,
        blocking: bool,
        timeout_s: float,
    ) -> ExecutionResult:
        try:
            body = (
                json.dumps(payload, sort_keys=True)
                if action.body is None
                else action.body.format_map(payload)
            )
            response = await self._client.request(
                action.method,
                action.url,
                content=body,
                headers=action.headers,
                timeout=timeout_s,
            )
            if not 200 <= response.status_code < 300:
                return ExecutionResult(err=RuntimeError(f"HTTP {response.status_code}"))
            if not blocking:
                return ExecutionResult()
            data = response.json()
            if data.get("decision") == "block":
                return ExecutionResult(blocked=True, reason=str(data.get("reason", "")))
            return ExecutionResult()
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return ExecutionResult(err=exc)

    @staticmethod
    def _run_subagent(action: SubagentAction, hook_name: str) -> None:
        print(
            f"[hook subagent] not yet implemented, skipped: {hook_name}",
            file=sys.stderr,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
