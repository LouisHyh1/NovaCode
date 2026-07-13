"""Context management orchestration."""

from dataclasses import dataclass
from enum import Enum

from novacode.compact.const import AUTO_COMPACT_TRIGGER_TOKENS
from novacode.compact.layer1 import offload_and_snip
from novacode.compact.layer2 import auto_compact, force_compact
from novacode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
)
from novacode.compact.token import estimate_tokens
from novacode.conversation import Conversation
from novacode.llm import Provider, ToolDefinition


class TriggerKind(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass
class ManageInput:
    conv: Conversation
    provider: Provider
    model: str
    context_window: int
    tool_defs: list[ToolDefinition]
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    usage_anchor: int
    anchor_msg_len: int
    estimated_token: int
    trigger: TriggerKind


@dataclass
class ManageOutput:
    before_tokens: int
    after_tokens: int


async def manage_context(in_: ManageInput) -> ManageOutput:
    before = in_.estimated_token
    if in_.trigger == TriggerKind.MANUAL:
        layer1_msgs = offload_and_snip(in_.conv.messages(), in_.replacement, in_.session)
        in_.conv.replace_history(layer1_msgs)
        new_msgs, before, after = await force_compact(in_)
        in_.conv.replace_history(new_msgs)
        return ManageOutput(before, after)

    layer1_msgs = offload_and_snip(in_.conv.messages(), in_.replacement, in_.session)
    in_.conv.replace_history(layer1_msgs)
    layer1_tokens = estimate_tokens(in_.usage_anchor, layer1_msgs, in_.anchor_msg_len)

    if in_.trigger == TriggerKind.EMERGENCY:
        new_msgs, before, after = await force_compact(
            ManageInput(**{**in_.__dict__, "estimated_token": layer1_tokens})
        )
        in_.conv.replace_history(new_msgs)
        return ManageOutput(before, after)

    if layer1_tokens < AUTO_COMPACT_TRIGGER_TOKENS or in_.auto_tracking.tripped():
        return ManageOutput(before, layer1_tokens)

    new_msgs, before, after = await auto_compact(
        ManageInput(**{**in_.__dict__, "estimated_token": layer1_tokens})
    )
    if after < before:
        in_.conv.replace_history(new_msgs)
    return ManageOutput(before, after)
