"""Layer 2: summarize conversation history and rebuild recovery context."""

import math

from novacode.compact.const import (
    PTL_DROP_PERCENTAGE,
    PTL_RETRY_LIMIT,
    RECENT_KEEP_MESSAGES,
    RECENT_KEEP_TOKENS,
)
from novacode.compact.recovery import build_recovery_attachment
from novacode.compact.summary_prompt import build_summary_prompt, extract_summary
from novacode.compact.token import estimate_tokens
from novacode.llm import Message, PromptTooLongError, Request


class CompactError(Exception):
    pass


COMPACT_SUMMARY_MARKER = "<!-- novacode:compact-summary -->"


def is_compact_summary(msg: Message) -> bool:
    return msg.role == "user" and msg.content.startswith(COMPACT_SUMMARY_MARKER)


def group_by_user_turn(msgs: list[Message]) -> list[list[Message]]:
    groups: list[list[Message]] = []
    current: list[Message] = []
    for msg in msgs:
        if msg.role == "user" and current:
            groups.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        groups.append(current)
    return groups


def _flatten(groups: list[list[Message]]) -> list[Message]:
    return [msg for group in groups for msg in group]


def pick_recent_tail(msgs: list[Message]) -> list[Message]:
    candidates = [msg for msg in msgs if not is_compact_summary(msg)]
    if not candidates:
        return []
    start = len(candidates)
    while start > 0:
        start -= 1
        tail = candidates[start:]
        if len(tail) >= RECENT_KEEP_MESSAGES and estimate_tokens(0, tail, 0) >= RECENT_KEEP_TOKENS:
            break

    while start > 0 and candidates[start].role == "tool":
        start -= 1
    if start > 0 and candidates[start].role == "assistant" and candidates[start].tool_calls:
        start -= 1
    return candidates[start:]


async def summarize_once(in_, msgs: list[Message]) -> str:
    text = ""
    req = Request(messages=build_summary_prompt(msgs), tools=[])
    async for ev in in_.provider.stream(req):
        if ev.err is not None:
            raise ev.err
        if ev.text:
            text += ev.text
    return extract_summary(text)


async def ptl_retry(in_, msgs: list[Message], first_err: Exception) -> str:
    groups = group_by_user_turn(msgs)
    err: Exception = first_err
    attempts = 0
    while groups:
        attempts += 1
        if attempts <= PTL_RETRY_LIMIT:
            drop = 1
        else:
            drop = max(1, math.ceil(len(groups) * PTL_DROP_PERCENTAGE))
        groups = groups[drop:]
        if not groups:
            break
        try:
            return await summarize_once(in_, _flatten(groups))
        except PromptTooLongError as exc:
            err = exc
            continue
    raise err


async def run_summary(in_) -> list[Message]:
    original = in_.conv.messages()
    recovery_snapshot = in_.recovery.snapshot()
    try:
        summary = await summarize_once(in_, original)
    except PromptTooLongError as exc:
        summary = await ptl_retry(in_, original, exc)

    attachment = build_recovery_attachment(recovery_snapshot, in_.tool_defs)
    tail = pick_recent_tail(original)
    first = Message(role="user", content=f"{COMPACT_SUMMARY_MARKER}\n{summary}\n\n{attachment}")
    return [first, *tail]


async def auto_compact(in_) -> tuple[list[Message], int, int]:
    try:
        new_msgs = await run_summary(in_)
    except Exception as exc:
        in_.auto_tracking.record_failure()
        raise CompactError(str(exc)) from exc
    in_.auto_tracking.record_success()
    return new_msgs, in_.estimated_token, estimate_tokens(0, new_msgs, 0)


async def force_compact(in_) -> tuple[list[Message], int, int]:
    new_msgs = await run_summary(in_)
    return new_msgs, in_.estimated_token, estimate_tokens(0, new_msgs, 0)
