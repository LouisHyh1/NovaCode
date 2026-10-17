"""SubAgent 定义目录及多来源覆盖。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from novacode.permission import Mode
from novacode.subagent.definition import Definition, Source
from novacode.subagent.embed import builtin_definitions
from novacode.subagent.parser import parse_file


class Catalog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._defs: dict[str, Definition] = {}
        self._by_source: dict[Source, list[Definition]] = {source: [] for source in Source}

    def _add_all(self, definitions: list[Definition], source: Source) -> None:
        with self._lock:
            for definition in definitions:
                definition.source = source
                current = self._defs.get(definition.name)
                if current is None or source >= current.source:
                    self._defs[definition.name] = definition
                self._by_source[source].append(definition)

    def resolve(self, name: str) -> Definition | None:
        with self._lock:
            return self._defs.get(name)

    def list(self) -> list[Definition]:
        with self._lock:
            return sorted(self._defs.values(), key=lambda item: item.name)

    def list_by_source(self, source: Source) -> list[Definition]:
        with self._lock:
            return list(self._by_source[source])

    def fork_definition(self) -> Definition:
        return Definition(
            name="__fork__",
            description="Fork-based subagent",
            max_turns=25,
            permission_mode=Mode.DEFAULT,
            source=Source.BUILTIN,
        )


def _load_from_dir(directory: Path, source: Source) -> list[Definition]:
    if not directory.is_dir():
        return []
    definitions: list[Definition] = []
    for path in sorted(directory.glob("*.md")):
        try:
            definitions.append(parse_file(path, source))
        except Exception as exc:
            print(f"subagent {path}: {exc}; skipped", file=sys.stderr)
    return definitions


def load_catalog(root: str | Path) -> Catalog:
    catalog = Catalog()
    catalog._add_all(builtin_definitions(), Source.BUILTIN)
    catalog._add_all(_load_from_dir(Path.home() / ".novacode" / "agents", Source.USER), Source.USER)
    catalog._add_all(
        _load_from_dir(Path(root) / ".novacode" / "agents", Source.PROJECT), Source.PROJECT
    )
    return catalog
