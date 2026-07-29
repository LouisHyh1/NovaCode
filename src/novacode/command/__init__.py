"""斜杠命令注册、解析与内置命令。"""

from novacode.command.builtins import register_builtins
from novacode.command.command import Command, Handler, Kind
from novacode.command.dispatch import arguments, parse
from novacode.command.registry import Registry
from novacode.command.ui import UI, NopUI

__all__ = [
    "Command",
    "Handler",
    "Kind",
    "NopUI",
    "Registry",
    "UI",
    "arguments",
    "parse",
    "register_builtins",
]
