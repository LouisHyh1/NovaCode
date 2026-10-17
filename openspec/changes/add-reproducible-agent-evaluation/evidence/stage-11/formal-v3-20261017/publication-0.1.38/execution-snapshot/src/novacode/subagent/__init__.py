"""SubAgent 角色定义与目录。"""

from novacode.subagent.catalog import Catalog, load_catalog
from novacode.subagent.definition import Definition, Source
from novacode.subagent.embed import builtin_definitions
from novacode.subagent.parser import DefinitionParseError, parse_definition, parse_file

__all__ = [
    "Catalog",
    "Definition",
    "DefinitionParseError",
    "Source",
    "builtin_definitions",
    "load_catalog",
    "parse_definition",
    "parse_file",
]
