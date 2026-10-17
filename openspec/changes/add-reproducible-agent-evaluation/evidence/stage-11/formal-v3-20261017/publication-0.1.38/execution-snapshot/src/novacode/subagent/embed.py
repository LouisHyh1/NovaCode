"""读取随包发布的内置 SubAgent 定义。"""

from importlib.resources import files

from novacode.subagent.definition import Definition, Source
from novacode.subagent.parser import parse_definition


def builtin_definitions() -> list[Definition]:
    package = files("novacode.subagent.builtin")
    definitions = [
        parse_definition(item.read_bytes(), f"builtin:{item.name}", Source.BUILTIN)
        for item in package.iterdir()
        if item.name.endswith(".md")
    ]
    return sorted(definitions, key=lambda item: item.name)
