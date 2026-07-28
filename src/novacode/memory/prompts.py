"""Structured memory prompt and response helpers."""

import json
from datetime import datetime
from typing import Any

from novacode.memory.types import MemoryAction, MemoryKind, MemoryTurn


def render_memory_indexes(user_index: str, project_index: str) -> str:
    """Render the two index snapshots without loading note bodies."""
    sections: list[str] = []
    if user_index:
        sections.append(f"## User memory index\n\n{user_index.rstrip()}")
    if project_index:
        sections.append(f"## Project memory index\n\n{project_index.rstrip()}")
    return "\n\n".join(sections)


def build_governance_prompt(*, now: datetime, target: str) -> str:
    return (
        "Consolidate only facts supported by the supplied sessions and memory indexes. "
        "Merge duplicates, delete obsolete entries, repair evidenced contradictions, "
        "replace relative dates with absolute dates, and keep the index within 200 lines "
        "and 25KB. Do not use shell commands or write outside the target memory directory.\n"
        f"Current date: {now.date().isoformat()}\nTarget memory directory: {target}"
    )


def build_extraction_prompt(turn: MemoryTurn, user_index: str, project_index: str) -> str:
    return (
        "Extract durable memory operations. Tools are forbidden. Return only a JSON array; "
        "never use prose or a code fence.\n"
        "Every object follows this contract: action is create, update, delete, or no-op; "
        "kind is user, feedback, project, or reference; memory_id, title, summary, and "
        "content are strings. Do not emit filename or file paths.\n"
        "Kind definitions: user = durable user facts/preferences; feedback = corrections "
        "about assistant behavior; project = durable repository facts/decisions; reference "
        "= durable external resources useful to the project.\n"
        "Required fields: create requires action, kind, title, summary, content and omits "
        "memory_id; update requires action, kind, memory_id and at least one of title, "
        "summary, content; delete requires only action, kind, memory_id; no-op requires only "
        "action.\n"
        "Fixed routing: user/feedback -> user store; project/reference -> project store. "
        "Use the complete indexes to merge duplicates, update conflicts, and avoid creating "
        "a second entry for the same fact.\n"
        "An explicit remember, update-memory, or forget request must not return an empty "
        "array. If the confirmed tool operation already made the index current, return one "
        "no-op object instead. For ordinary turns with no durable information, return [].\n\n"
        f"User index:\n{user_index or '(empty)'}\n\n"
        f"Project index:\n{project_index or '(empty)'}\n\n"
        f"Latest user message:\n{turn.user_content}\n\n"
        f"Final assistant reply:\n{turn.assistant_content}"
    )


def parse_actions(text: str) -> list[MemoryAction]:
    raw: Any = json.loads(text)
    if isinstance(raw, dict):
        raw = raw.get("actions")
    if not isinstance(raw, list):
        raise ValueError("memory response must be an action list")
    actions: list[MemoryAction] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("memory action must be an object")
        kind_value = item.get("kind")
        kind = MemoryKind(kind_value) if kind_value is not None else None
        actions.append(
            MemoryAction(
                action=item.get("action", ""),
                kind=kind,
                memory_id=item.get("memory_id", ""),
                title=item.get("title", ""),
                summary=item.get("summary", ""),
                content=item.get("content", ""),
                filename=item.get("filename", ""),
            )
        )
    return actions
