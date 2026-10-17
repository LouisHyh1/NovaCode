"""Stateless JSONL message encoding."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from novacode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolCall,
    ToolResult,
)


def message_payload(message: Message) -> dict[str, Any]:
    if message.role not in {ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL}:
        raise ValueError(f"invalid message role: {message.role}")
    if not isinstance(message.content, str):
        raise TypeError("message content must be a string")

    payload: dict[str, Any] = {"role": message.role}
    if message.content or message.role != ROLE_TOOL:
        payload["content"] = message.content
    if message.tool_calls:
        payload["tool_calls"] = [
            {"id": call.id, "name": call.name, "input": call.input} for call in message.tool_calls
        ]
    if message.tool_results:
        payload["tool_results"] = [
            {
                "tool_call_id": result.tool_call_id,
                "content": result.content,
                "is_error": result.is_error,
                "is_policy_denial": result.is_policy_denial,
            }
            for result in message.tool_results
        ]
    return payload


def encode_message(
    message: Message,
    *,
    model: str = "",
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "message",
        "ts": (timestamp or datetime.now(UTC)).astimezone(UTC).isoformat(),
        **message_payload(message),
    }
    if model:
        record["model"] = model
    return record


def decode_message(record: dict[str, Any]) -> Message:
    role = record.get("role")
    if role not in {ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL}:
        raise ValueError("invalid message role")
    content = record.get("content", "")
    if not isinstance(content, str):
        raise ValueError("invalid message content")

    raw_calls = record.get("tool_calls", [])
    raw_results = record.get("tool_results", [])
    if not isinstance(raw_calls, list) or not isinstance(raw_results, list):
        raise ValueError("invalid tool data")
    calls: list[ToolCall] = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            raise ValueError("invalid tool call")
        call_id, name, input_ = raw.get("id"), raw.get("name"), raw.get("input")
        if not all(isinstance(value, str) and value for value in (call_id, name, input_)):
            raise ValueError("invalid tool call")
        calls.append(ToolCall(id=call_id, name=name, input=input_))

    results: list[ToolResult] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise ValueError("invalid tool result")
        call_id, result_content = raw.get("tool_call_id"), raw.get("content")
        is_error = raw.get("is_error", False)
        policy_denial = raw.get("is_policy_denial", False)
        if not isinstance(call_id, str) or not call_id or not isinstance(result_content, str):
            raise ValueError("invalid tool result")
        if not isinstance(is_error, bool) or not isinstance(policy_denial, bool):
            raise ValueError("invalid tool result state")
        results.append(
            ToolResult(
                tool_call_id=call_id,
                content=result_content,
                is_error=is_error,
                is_policy_denial=policy_denial,
            )
        )

    if role == ROLE_TOOL and (calls or not results):
        raise ValueError("invalid tool message")
    if role != ROLE_TOOL and results:
        raise ValueError("tool results require tool role")
    if role != ROLE_ASSISTANT and calls:
        raise ValueError("tool calls require assistant role")
    return Message(role=role, content=content, tool_calls=calls, tool_results=results)


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing record timestamp")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("record timestamp must include timezone")
    return timestamp.astimezone(UTC)


def canonical_json(record: Any) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def replacement_digest(messages: list[Message]) -> str:
    payload = [message_payload(message) for message in messages]
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()
