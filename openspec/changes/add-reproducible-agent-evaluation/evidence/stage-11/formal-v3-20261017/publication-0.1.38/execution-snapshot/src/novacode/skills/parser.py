"""Skill frontmatter 解析。"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
VALID_MODES = {"inline", "fork"}
VALID_CONTEXTS = {"full", "recent", "none"}


class SkillParseError(ValueError):
    """Skill 文件格式无效。"""


@dataclass(frozen=True)
class SkillDef:
    name: str
    description: str
    prompt_body: str
    mode: str = "inline"
    model: str | None = None
    context: str = "full"
    source_path: Path | None = None
    is_directory: bool = False


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """拆分 ``---`` YAML frontmatter 与正文。"""
    normalized = raw.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise SkillParseError("missing opening frontmatter delimiter")
    end = normalized.find("\n---", 4)
    if end < 0:
        raise SkillParseError("unclosed frontmatter")
    after = end + 4
    if after < len(normalized) and normalized[after] not in "\n":
        raise SkillParseError("invalid closing frontmatter delimiter")
    try:
        meta = yaml.safe_load(normalized[4:end])
    except yaml.YAMLError as exc:
        raise SkillParseError(f"invalid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillParseError("frontmatter must be a mapping")
    body = normalized[after:].lstrip("\n")
    return meta, body


def _validate_meta(meta: dict[str, Any]) -> tuple[str, str, str, str | None, str]:
    name = meta.get("name")
    description = meta.get("description")
    mode = meta.get("mode", "inline")
    model = meta.get("model")
    context = meta.get("context", "full")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise SkillParseError("name must match ^[a-z][a-z0-9-]*$")
    if not isinstance(description, str) or not description.strip():
        raise SkillParseError("description must be a non-empty string")
    if mode not in VALID_MODES:
        raise SkillParseError("mode must be inline or fork")
    if context not in VALID_CONTEXTS:
        raise SkillParseError("context must be full, recent, or none")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise SkillParseError("model must be a non-empty string or null")
    return name, description.strip(), mode, model, context


def parse_skill_file(path: str | Path, *, is_directory: bool | None = None) -> SkillDef:
    source_path = Path(path).resolve()
    try:
        raw = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillParseError(f"cannot read skill: {exc}") from exc
    meta, body = parse_frontmatter(raw)
    name, description, mode, model, context = _validate_meta(meta)
    directory_layout = source_path.name == "SKILL.md" if is_directory is None else is_directory
    return SkillDef(
        name=name,
        description=description,
        prompt_body=body,
        mode=mode,
        model=model,
        context=context,
        source_path=source_path,
        is_directory=directory_layout,
    )


def substitute_arguments(prompt_body: str, args: str) -> str:
    return prompt_body.replace("$ARGUMENTS", args)
