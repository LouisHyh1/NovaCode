"""Project instruction loading and safe reference expansion."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_REFERENCE_RE = re.compile(r"@(.+)")


@dataclass(frozen=True)
class InstructionLoader:
    project_root: Path
    user_root: Path
    max_reference_depth: int = 5

    def load(self) -> str:
        project_root = self.project_root.resolve()
        user_root = self.user_root.resolve()
        roots = (
            (self.user_root / "NOVACODE.md", user_root),
            (self.project_root / "NOVACODE.md", project_root),
            (self.project_root / ".novacode" / "NOVACODE.md", project_root),
            (self.project_root / "NOVACODE.local.md", project_root),
        )
        layers: list[str] = []
        for path, boundary in roots:
            if not path.exists():
                continue
            content = self._expand(path, boundary, 0, ())
            if content.strip():
                layers.append(content)
        return "\n---\n".join(layers)

    def _expand(
        self,
        path: Path,
        boundary: Path,
        depth: int,
        chain: tuple[Path, ...],
    ) -> str:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            self._warn("instruction file unavailable", path, exc)
            return ""
        if not resolved.is_relative_to(boundary):
            self._warn("outside instruction boundary", resolved)
            return ""
        if resolved in chain:
            self._warn("reference cycle", resolved)
            return ""
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            self._warn("instruction file unavailable", resolved, exc)
            return ""
        if b"\x00" in raw:
            self._warn("binary instruction file", resolved)
            return ""
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._warn("binary instruction file", resolved, exc)
            return ""

        expanded: list[str] = []
        next_chain = (*chain, resolved)
        for line in text.splitlines():
            match = _REFERENCE_RE.fullmatch(line)
            if match is None:
                expanded.append(line)
                continue
            if depth >= self.max_reference_depth:
                self._warn("reference depth exceeded", resolved)
                continue
            reference = Path(match.group(1))
            if reference.is_absolute():
                self._warn("outside instruction boundary", reference)
                continue
            content = self._expand(
                resolved.parent / reference,
                boundary,
                depth + 1,
                next_chain,
            )
            if content:
                expanded.append(content)
        return "\n".join(expanded)

    @staticmethod
    def _warn(reason: str, path: Path, exc: Exception | None = None) -> None:
        if exc is None:
            logger.warning("%s: %s", reason, path)
        else:
            logger.warning("%s: %s (%s)", reason, path, type(exc).__name__)
