"""OpenAI Chat Completions API adapter with streaming and tool calls."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from novacode.config import ProviderConfig
from novacode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    Usage,
)
from novacode.llm.metadata import client_options, raw_usage, request_metadata, ui_count


class OpenAIProvider:
    def __init__(self, cfg: ProviderConfig) -> None:
        from openai import AsyncOpenAI

        self._name = cfg.name
        self._model = cfg.model
        self._config = cfg
        self._client = AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
            **client_options(cfg),
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def close(self) -> None:
        await self._client.close()

    def request_metadata(self, req: Request) -> dict[str, Any]:
        return request_metadata(self._config, req)

    async def stream(self, req: Request) -> "AsyncIterator[StreamEvent]":
        params = self._request_params(req)
        s = None
        try:
            s = await self._client.chat.completions.create(**params)
            tool_calls_buf: dict[int, dict[str, str]] = {}
            async for chunk in s:
                # 末尾 usage chunk（choices 空，带 chunk.usage）
                if not chunk.choices:
                    if chunk.usage is not None:
                        yield StreamEvent(usage=_usage_from_openai(chunk.usage))
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield StreamEvent(text=delta.content)
                if delta.tool_calls:
                    self._merge_tool_deltas(delta.tool_calls, tool_calls_buf)
            calls = self._tool_calls(tool_calls_buf)
            if calls:
                yield StreamEvent(tool_calls=calls)
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            yield StreamEvent(err=_wrap_prompt_too_long(e))
        finally:
            if s is not None:
                await s.close()

    @staticmethod
    def _merge_tool_deltas(deltas: Any, buffers: dict[int, dict[str, str]]) -> None:
        for delta in deltas:
            buf = buffers.setdefault(delta.index, {"id": "", "name": "", "args": ""})
            if delta.id:
                buf["id"] = delta.id
            if delta.function and delta.function.name:
                buf["name"] = delta.function.name
            if delta.function and delta.function.arguments:
                buf["args"] += delta.function.arguments

    @staticmethod
    def _tool_calls(buffers: dict[int, dict[str, str]]) -> list[ToolCall]:
        return [
            ToolCall(id=v["id"], name=v["name"], input=v.get("args") or "{}")
            for _, v in sorted(buffers.items())
        ]

    def _request_params(self, req: Request) -> dict[str, Any]:
        messages = self._to_openai_messages(req)
        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if req.tools:
            params["tools"] = self._to_openai_tools(req.tools)
        if self._config.max_output_tokens is not None:
            params["max_tokens"] = self._config.max_output_tokens

        return params

    def _to_openai_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    def _to_openai_messages(self, req: Request) -> list[dict]:
        """构造 OpenAI 消息列表。

        - 首条 system 消息 = stable + environment 拼接（stable 居前）
        - 随即映射对话历史
        - reminder 非空时追加尾部 user 消息
        """
        # 系统消息（stable 在前，兼容端点前缀缓存）
        system_text = req.system.stable
        if req.system.environment:
            if system_text:
                system_text = system_text + "\n\n" + req.system.environment
            else:
                system_text = req.system.environment
        result: list[dict] = [{"role": "system", "content": system_text}]

        # 对话历史
        for m in req.messages:
            if m.role == ROLE_USER:
                result.append({"role": "user", "content": m.content})
            elif m.role == ROLE_ASSISTANT:
                if m.tool_calls:
                    tc_list = []
                    for c in m.tool_calls:
                        tc_list.append(
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {
                                    "name": c.name,
                                    "arguments": c.input or "{}",
                                },
                            }
                        )
                    result.append(
                        {
                            "role": "assistant",
                            "content": m.content or None,
                            "tool_calls": tc_list,
                        }
                    )
                else:
                    result.append({"role": "assistant", "content": m.content})
            elif m.role == ROLE_TOOL:
                for r in m.tool_results:
                    result.append(
                        {
                            "role": "tool",
                            "tool_call_id": r.tool_call_id,
                            "content": r.content,
                        }
                    )

        # 补充消息注入（尾部 user 消息，OpenAI 容忍连续 user）
        if req.reminder:
            result.append({"role": "user", "content": req.reminder})

        return result


def _usage_from_openai(raw) -> Usage:
    input_tokens = ui_count(getattr(raw, "prompt_tokens", None))
    output_tokens = ui_count(getattr(raw, "completion_tokens", None))
    cache_read = ui_count(
        getattr(getattr(raw, "prompt_tokens_details", None), "cached_tokens", None)
    )
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_write=0,
        cache_read=cache_read,
        context_tokens=input_tokens + output_tokens,
        raw=raw_usage(raw, "openai"),
        normalization="openai-cache-reasoning-subsets-v1",
        protocol="openai",
    )


def _wrap_prompt_too_long(exc: Exception) -> Exception:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status in (400, 413) and (
        "maximum context" in text or "context length" in text or "prompt is too long" in text
    ):
        return PromptTooLongError(str(exc))
    return exc
