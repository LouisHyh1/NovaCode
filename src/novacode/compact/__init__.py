"""Context compaction support."""

from novacode.compact.compact import ManageInput, ManageOutput, TriggerKind, manage_context
from novacode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
    new_session_context,
)

__all__ = [
    "CompactCircuitBreaker",
    "ContentReplacementState",
    "ManageInput",
    "ManageOutput",
    "RecoveryState",
    "SessionContext",
    "TriggerKind",
    "manage_context",
    "new_session_context",
]
