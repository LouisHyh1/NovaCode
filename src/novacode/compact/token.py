"""Token estimation helpers."""

import math

from novacode.compact.const import ESTIMATE_CHARS_PER_TOKEN
from novacode.llm import Message, Usage


def usage_anchor(u: Usage) -> int:
    return u.input_tokens + u.output_tokens + u.cache_write + u.cache_read


def message_chars(msgs: list[Message]) -> int:
    total = 0
    for msg in msgs:
        total += len(msg.content.encode("utf-8"))
        for call in msg.tool_calls:
            total += len(call.input.encode("utf-8"))
            total += len(call.name.encode("utf-8"))
            total += len(call.id.encode("utf-8"))
        for result in msg.tool_results:
            total += len(result.content.encode("utf-8"))
            total += len(result.tool_call_id.encode("utf-8"))
    return total


def estimate_tokens(anchor: int, all_msgs: list[Message], anchor_msg_len: int) -> int:
    if anchor_msg_len < 0:
        anchor_msg_len = 0
    delta = all_msgs[anchor_msg_len:]
    return int(anchor) + math.ceil(message_chars(delta) / ESTIMATE_CHARS_PER_TOKEN)
