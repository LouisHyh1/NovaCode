"""Hook 动作执行器。"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from novacode.hook.rule import (
    HttpAction,
    Payload,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)
from novacode.llm import Provider

if TYPE_CHECKING:
    from novacode.agent import Agent
    from novacode.subagent import Catalog


@dataclass
class ExecutionResult:
    blocked: bool = False
    reason: str = ""
    prompt: str = ""
    output: str = ""
    err: Exception | None = None


class Executor:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._provider: Provider | None = None
        self._parent: Agent | None = None
        self._catalog: Catalog | None = None

    @property
    def provider(self) -> Provider | None:
        return self._provider

    def bind_provider(self, provider: Provider) -> None:
        if self._provider is not None and self._provider is not provider:
            raise RuntimeError("hook executor cannot bind a different provider")
        self._provider = provider

    def bind_subagent_runtime(self, parent: Agent, catalog: Catalog) -> None:
        self.bind_provider(parent.provider)
        self._parent = parent
        self._catalog = catalog

    async def run(self, rule: Rule, payload: Payload, *, blocking: bool) -> ExecutionResult:
        try:
            if isinstance(rule.action, ShellAction):
                return await self._run_shell(rule.action, payload, blocking, rule.timeout_s)
            if isinstance(rule.action, PromptAction):
                return ExecutionResult(prompt=rule.action.text)
            if isinstance(rule.action, HttpAction):
                return await self._run_http(rule.action, payload, blocking, rule.timeout_s)
            return await self._run_subagent(rule.action, rule.timeout_s)
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

    async def _run_subagent(self, action: SubagentAction, timeout_s: float) -> ExecutionResult:
        parent = self._parent
        catalog = self._catalog
        if parent is None or catalog is None:
            raise RuntimeError("subagent hook runtime is not bound")
        definition = catalog.resolve(action.agent_name)
        if definition is None:
            raise ValueError(f"unknown hook subagent: {action.agent_name}")

        from novacode.agent import Agent
        from novacode.conversation import Conversation
        from novacode.permission import Mode

        allowed = [item.name for item in parent.registry.definitions()]
        if definition.tools:
            configured = set(definition.tools)
            allowed = [name for name in allowed if name in configured]
        denied = set(definition.disallowed_tools)
        allowed = [name for name in allowed if name not in denied]
        agent = Agent(
            parent.provider,
            parent.registry,
            parent.version,
            parent.engine,
            context_window=parent.context_window,
            context_compression=parent.context_compression,
            instructions=parent.instructions,
            memory_index=parent.memory_index,
            hook_engine=None,
            system_prompt=definition.system_prompt,
            max_turns=definition.max_turns,
            permission_mode=Mode.PLAN,
            dont_ask=True,
            allowed_tools=allowed,
            subagent_name=definition.name,
        )
        output = await asyncio.wait_for(
            agent.run_to_completion(Conversation(), action.prompt),
            timeout=timeout_s,
        )
        return ExecutionResult(output=output)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
