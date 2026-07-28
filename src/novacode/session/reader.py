"""JSONL session recovery."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novacode.llm import ROLE_ASSISTANT, ROLE_TOOL, Message
from novacode.session.codec import decode_message, parse_timestamp, replacement_digest
from novacode.session.types import SessionLoadResult


@dataclass
class _PendingCompaction:
    transaction_id: str
    count: int
    digest: str
    messages: list[Message] = field(default_factory=list)


def load_session(path: Path) -> SessionLoadResult:
    path = Path(path)
    messages: list[Message] = []
    diagnostics: list[str] = []
    model = ""
    last_activity = None
    pending: _PendingCompaction | None = None

    try:
        lines = path.read_bytes().splitlines()
    except OSError as exc:
        raise OSError(f"read session failed: {path}") from exc

    for line_number, raw in enumerate(lines, 1):
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            diagnostics.append(f"line {line_number}: invalid JSON")
            continue
        if not isinstance(record, dict):
            diagnostics.append(f"line {line_number}: invalid record")
            continue
        type_ = record.get("type")
        try:
            timestamp = parse_timestamp(record.get("ts"))
            if type_ == "message":
                if pending is not None:
                    diagnostics.append("uncommitted compact transaction ignored")
                    pending = None
                message = decode_message(record)
                if not model:
                    raw_model = record.get("model", "")
                    if not isinstance(raw_model, str):
                        raise ValueError("invalid model")
                    model = raw_model
                messages.append(message)
            elif type_ == "compact_begin":
                pending = _begin(record)
            elif type_ == "compact_message":
                if pending is None or not _append_compact_message(pending, record):
                    diagnostics.append(f"line {line_number}: invalid compact message")
            elif type_ == "compact_commit":
                if pending is not None and _commit_matches(pending, record):
                    messages = pending.messages
                else:
                    diagnostics.append(f"line {line_number}: invalid compact commit")
                pending = None
            else:
                raise ValueError("unknown record type")
        except (TypeError, ValueError):
            diagnostics.append(f"line {line_number}: invalid {type_ or 'record'}")
            continue
        last_activity = timestamp

    if pending is not None:
        diagnostics.append("uncommitted compact transaction ignored")
    messages = _truncate_incomplete_tool_chain(messages, diagnostics)
    return SessionLoadResult(path.stem, messages, model, last_activity, diagnostics)


def _begin(record: dict[str, Any]) -> _PendingCompaction:
    transaction_id = record.get("transaction_id")
    count = record.get("count")
    digest = record.get("digest")
    if not isinstance(transaction_id, str) or not transaction_id:
        raise ValueError("invalid transaction ID")
    if not isinstance(count, int) or count < 0:
        raise ValueError("invalid compact count")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("invalid compact digest")
    return _PendingCompaction(transaction_id, count, digest)


def _append_compact_message(pending: _PendingCompaction, record: dict[str, Any]) -> bool:
    if record.get("transaction_id") != pending.transaction_id:
        return False
    if record.get("sequence") != len(pending.messages):
        return False
    raw_message = record.get("message")
    if not isinstance(raw_message, dict):
        return False
    try:
        pending.messages.append(decode_message(raw_message))
    except ValueError:
        return False
    return True


def _commit_matches(pending: _PendingCompaction, record: dict[str, Any]) -> bool:
    return (
        record.get("transaction_id") == pending.transaction_id
        and record.get("count") == pending.count == len(pending.messages)
        and record.get("digest") == pending.digest == replacement_digest(pending.messages)
    )


def _truncate_incomplete_tool_chain(
    messages: list[Message], diagnostics: list[str]
) -> list[Message]:
    valid: list[Message] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == ROLE_TOOL:
            diagnostics.append("orphan tool results; history truncated")
            return valid
        if message.role == ROLE_ASSISTANT and message.tool_calls:
            if index + 1 >= len(messages) or messages[index + 1].role != ROLE_TOOL:
                diagnostics.append("incomplete tool chain; history truncated")
                return valid
            result_message = messages[index + 1]
            call_ids = [call.id for call in message.tool_calls]
            result_ids = [result.tool_call_id for result in result_message.tool_results]
            if call_ids != result_ids:
                diagnostics.append("mismatched tool chain; history truncated")
                return valid
            valid.extend((message, result_message))
            index += 2
            continue
        valid.append(message)
        index += 1
    return valid
