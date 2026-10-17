"""项目级与用户级 Skill 加载。"""

import logging
from pathlib import Path

from novacode.skills.parser import SkillDef, SkillParseError, parse_skill_file

PROJECT_SKILLS_DIR = ".novacode/skills"
USER_SKILLS_DIR = "~/.novacode/skills"

log = logging.getLogger(__name__)


class SkillLoader:
    def __init__(self, work_dir: str | Path) -> None:
        self.work_dir = Path(work_dir).resolve()
        self._project_dir = self.work_dir / PROJECT_SKILLS_DIR
        self._user_dir = Path(USER_SKILLS_DIR).expanduser().resolve()
        self._skills: dict[str, SkillDef] = {}
        self._cache: dict[str, SkillDef] = {}

    def load_all(self) -> dict[str, SkillDef]:
        loaded: dict[str, SkillDef] = {}
        for directory, source in ((self._project_dir, "project"), (self._user_dir, "user")):
            for skill in self._scan_directory(directory, source):
                loaded.setdefault(skill.name, skill)
        self._skills = loaded
        self._cache = dict(loaded)
        return dict(loaded)

    def reload(self) -> dict[str, SkillDef]:
        return self.load_all()

    def _scan_directory(self, path: Path, source: str) -> list[SkillDef]:
        if not path.is_dir():
            return []
        candidates = [(item, False) for item in sorted(path.glob("*.md"))]
        candidates.extend(
            (item / "SKILL.md", True)
            for item in sorted(path.iterdir())
            if item.is_dir() and (item / "SKILL.md").is_file()
        )
        skills: list[SkillDef] = []
        for skill_path, is_directory in candidates:
            try:
                skills.append(parse_skill_file(skill_path, is_directory=is_directory))
            except SkillParseError as exc:
                log.warning("Skipping %s skill '%s': %s", source, skill_path, exc)
        return skills

    def get(self, name: str) -> SkillDef | None:
        skill = self._skills.get(name)
        if skill is None or skill.source_path is None:
            return None
        try:
            fresh = parse_skill_file(skill.source_path, is_directory=skill.is_directory)
        except SkillParseError as exc:
            log.warning("Failed to reload skill '%s'; using cached version: %s", name, exc)
            return self._cache.get(name)
        if fresh.name != name:
            log.warning("Failed to reload skill '%s'; name changed to '%s'", name, fresh.name)
            return self._cache.get(name)
        self._skills[name] = fresh
        self._cache[name] = fresh
        return fresh

    def get_catalog(self) -> list[tuple[str, str]]:
        return [(skill.name, skill.description) for skill in self._skills.values()]

    def get_source_label(self, name: str) -> str:
        skill = self._skills.get(name)
        if skill is None or skill.source_path is None:
            return "unknown"
        try:
            skill.source_path.relative_to(self._project_dir)
            return "project"
        except ValueError:
            pass
        try:
            skill.source_path.relative_to(self._user_dir)
            return "user"
        except ValueError:
            return "unknown"

    def names(self) -> list[str]:
        return list(self._skills)
