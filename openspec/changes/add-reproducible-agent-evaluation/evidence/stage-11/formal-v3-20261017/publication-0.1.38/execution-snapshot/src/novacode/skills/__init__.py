"""Skill 系统公开接口。"""

from novacode.skills.executor import SkillExecutor
from novacode.skills.install import install_skill, parse_skill_url
from novacode.skills.loader import SkillLoader
from novacode.skills.parser import (
    SkillDef,
    SkillParseError,
    parse_frontmatter,
    parse_skill_file,
    substitute_arguments,
)

__all__ = [
    "SkillDef",
    "SkillExecutor",
    "SkillLoader",
    "SkillParseError",
    "parse_frontmatter",
    "parse_skill_url",
    "parse_skill_file",
    "install_skill",
    "substitute_arguments",
]
