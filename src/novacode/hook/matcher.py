"""把共用 Matcher 应用到 Hook payload 字段。"""

from __future__ import annotations

import json
from typing import Any

from novacode.hook.rule import CombineMode, Condition, Payload


def get_by_path(payload: Payload, path: str) -> str:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return ""
        value = value[part]
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True)


def eval_condition(condition: Condition | None, payload: Payload) -> bool:
    if condition is None:
        return True
    matches = (atom.matcher.match(get_by_path(payload, atom.field)) for atom in condition.atoms)
    if condition.mode is CombineMode.ALL_OF:
        return all(matches)
    return any(matches)
