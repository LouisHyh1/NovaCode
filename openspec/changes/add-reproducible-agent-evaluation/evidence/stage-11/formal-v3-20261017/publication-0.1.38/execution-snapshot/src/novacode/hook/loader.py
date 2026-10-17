"""从项目级与用户级 YAML 加载 Hook。"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from novacode.hook.engine import Engine
from novacode.hook.event import is_blocking, parse_event
from novacode.hook.rule import (
    AtomCondition,
    CombineMode,
    Condition,
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)
from novacode.permission.matcher import (
    ExactMatcher,
    GlobMatcher,
    Matcher,
    NotMatcher,
    RegexMatcher,
)

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([smh]?)$")


def load(project_root: str | Path) -> Engine:
    """加载 `.novacode/hooks.yaml`；坏文件或坏规则只报 stderr。"""
    candidates = [
        Path(project_root) / ".novacode" / "hooks.yaml",
        Path.home() / ".novacode" / "hooks.yaml",
    ]
    rules: list[Rule] = []
    sources: list[str] = []
    names: set[str] = set()
    for path in candidates:
        if not path.is_file():
            continue
        raw = _read_file(path)
        if raw is None:
            continue
        sources.append(str(path))
        for index, item in enumerate(raw, start=1):
            try:
                rule = _compile_rule(path, item)
                if rule.name in names:
                    raise ValueError(f'duplicate name "{rule.name}", skipped')
            except ValueError as exc:
                _report(item, index, exc)
                continue
            names.add(rule.name)
            rules.append(rule)
    return Engine(rules, sources)


def _read_file(path: Path) -> list[Any] | None:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        print(f"hooks file {path} load failed: {exc}", file=sys.stderr)
        return None
    if not isinstance(document, dict) or not isinstance(document.get("hooks"), list):
        print(f"hooks file {path} invalid: top-level hooks must be a list", file=sys.stderr)
        return None
    return document["hooks"]


def _compile_rule(source: Path, raw: Any) -> Rule:
    if not isinstance(raw, dict):
        raise ValueError("hook must be a mapping, skipped")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string, skipped")
    name = name.strip()
    event_name = raw.get("event")
    if not isinstance(event_name, str) or (event := parse_event(event_name)) is None:
        raise ValueError(f'unknown event "{event_name}", skipped')
    action = _compile_action(raw.get("action"))
    condition = _compile_condition(raw.get("if"))
    only_once = _bool_field(raw, "only_once", False)
    asyncio_mode = _bool_field(raw, "async", False)
    if asyncio_mode and is_blocking(event):
        raise ValueError("async not allowed for blocking events, skipped")
    return Rule(
        name=name,
        event=event,
        action=action,
        condition=condition,
        only_once=only_once,
        asyncio_mode=asyncio_mode,
        timeout_s=_parse_duration(raw.get("timeout", "30s")),
        source=str(source),
    )


def _compile_condition(raw: Any) -> Condition | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("if must be a mapping, skipped")
    keys = [key for key in ("all_of", "any_of") if key in raw]
    if len(keys) != 1 or len(raw) != 1:
        raise ValueError("if must contain exactly one of all_of or any_of, skipped")
    atoms_raw = raw[keys[0]]
    if not isinstance(atoms_raw, list):
        raise ValueError(f"if.{keys[0]} must be a list, skipped")
    atoms: list[AtomCondition] = []
    for atom in atoms_raw:
        if not isinstance(atom, dict):
            raise ValueError("condition atom must be a mapping, skipped")
        field = atom.get("field")
        if not isinstance(field, str) or not field:
            raise ValueError("condition field must be a non-empty string, skipped")
        atoms.append(AtomCondition(field, _compile_match(atom.get("match"))))
    return Condition(CombineMode(keys[0]), atoms)


def _compile_match(raw: Any) -> Matcher:
    if not isinstance(raw, dict):
        raise ValueError("condition match must be a mapping, skipped")
    kind = raw.get("type")
    if kind == "not":
        if "inner" not in raw:
            raise ValueError("not matcher requires inner, skipped")
        return NotMatcher(_compile_match(raw["inner"]))
    value = raw.get("value")
    if not isinstance(value, str):
        raise ValueError(f"{kind or 'unknown'} matcher requires string value, skipped")
    if kind == "exact":
        return ExactMatcher(value)
    if kind == "glob":
        if not value:
            raise ValueError("glob matcher value cannot be empty, skipped")
        return GlobMatcher(value)
    if kind == "regex":
        try:
            return RegexMatcher(value, re.compile(value))
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}, skipped") from exc
    raise ValueError(f'unknown matcher type "{kind}", skipped')


def _compile_action(raw: Any):
    if not isinstance(raw, dict):
        raise ValueError("action must be a mapping, skipped")
    kind = raw.get("type")
    if kind == "shell":
        return ShellAction(_required_string(raw, "command"))
    if kind == "prompt":
        return PromptAction(_required_string(raw, "text"))
    if kind == "http":
        headers = raw.get("headers", {})
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
        ):
            raise ValueError("http headers must be string pairs, skipped")
        body = raw.get("body")
        if body is not None and not isinstance(body, str):
            raise ValueError("http body must be a string, skipped")
        method = raw.get("method", "POST")
        if not isinstance(method, str) or not method:
            raise ValueError("http method must be a non-empty string, skipped")
        return HttpAction(_required_string(raw, "url"), method.upper(), dict(headers), body)
    if kind == "subagent":
        return SubagentAction(_required_string(raw, "agent_name"), _required_string(raw, "prompt"))
    raise ValueError(f'unknown action type "{kind}", skipped')


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"action.{key} must be a non-empty string, skipped")
    return value


def _bool_field(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean, skipped")
    return value


def _parse_duration(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    if not isinstance(value, str) or (match := _DURATION.fullmatch(value)) is None:
        raise ValueError(f'invalid timeout "{value}", skipped')
    amount = float(match.group(1))
    if amount <= 0:
        raise ValueError(f'invalid timeout "{value}", skipped')
    factor = {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]
    return amount * factor


def _report(raw: Any, index: int, exc: ValueError) -> None:
    name = raw.get("name") if isinstance(raw, dict) else None
    prefix = f'hook "{name}"' if isinstance(name, str) and name else f"hook #{index}"
    print(f"{prefix}: {exc}", file=sys.stderr)
