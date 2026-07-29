"""SubAgent Markdown 定义解析。"""

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from novacode.permission import Mode, parse_mode
from novacode.subagent.definition import Definition, Source

AGENT_NAME_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
VALID_MODELS = {"inherit", "haiku", "sonnet", "opus"}


class DefinitionParseError(ValueError):
    """Agent 定义格式无效。"""


def parse_frontmatter_and_body(data: bytes) -> tuple[dict[str, Any], str]:
    text = data.decode("utf-8-sig").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise DefinitionParseError("missing opening frontmatter delimiter")
    end = text.find("\n---", 4)
    if end < 0:
        raise DefinitionParseError("unclosed frontmatter")
    after = end + 4
    if after < len(text) and text[after] != "\n":
        raise DefinitionParseError("invalid closing frontmatter delimiter")
    try:
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise DefinitionParseError(f"invalid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise DefinitionParseError("frontmatter must be a mapping")
    return meta, text[after:].lstrip("\n")


def _string_list(meta: dict[str, Any], key: str) -> list[str]:
    value = meta.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DefinitionParseError(f"{key} must be a list of strings")
    return [item for item in value if item]


def parse_definition(data: bytes, file_path: str, source: Source) -> Definition:
    meta, body = parse_frontmatter_and_body(data)
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not AGENT_NAME_REGEX.fullmatch(name.strip()):
        raise DefinitionParseError("name must match ^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    if not isinstance(description, str) or not description.strip():
        raise DefinitionParseError("description must be a non-empty string")

    model = str(meta.get("model") or "inherit").strip()
    if model not in VALID_MODELS:
        print(
            f'subagent {file_path}: unknown model "{model}", defaulting to inherit',
            file=sys.stderr,
        )
        model = "inherit"

    raw_mode = str(meta.get("permissionMode") or "default").strip()
    dont_ask = raw_mode.lower() == "dontask"
    permission_mode = Mode.DEFAULT
    if not dont_ask:
        permission_mode, ok = parse_mode(raw_mode)
        if not ok:
            print(
                f'subagent {file_path}: unknown permissionMode "{raw_mode}", defaulting to default',
                file=sys.stderr,
            )
            permission_mode = Mode.DEFAULT

    raw_turns = meta.get("maxTurns", 0)
    if type(raw_turns) is not int or raw_turns < 0:
        raise DefinitionParseError("maxTurns must be a non-negative integer")
    raw_background = meta.get("background", False)
    if type(raw_background) is not bool:
        raise DefinitionParseError("background must be a boolean")

    return Definition(
        name=name.strip(),
        description=description.strip(),
        tools=_string_list(meta, "tools"),
        disallowed_tools=_string_list(meta, "disallowedTools"),
        model=model,
        max_turns=raw_turns,
        permission_mode=permission_mode,
        dont_ask=dont_ask,
        background=raw_background,
        system_prompt=body,
        file_path=file_path,
        source=source,
    )


def parse_file(path: str | Path, source: Source) -> Definition:
    source_path = Path(path).resolve()
    return parse_definition(source_path.read_bytes(), str(source_path), source)
