"""命令处理器可访问的最小 UI 能力。"""

from dataclasses import dataclass
from typing import Protocol

from novacode.hook.rule import Rule as HookRule
from novacode.permission import Mode


@dataclass
class WorktreeSummary:
    name: str
    path: str
    branch: str
    active: bool
    manual: bool


class WorktreeAccessor(Protocol):
    async def create(self, name: str) -> tuple[str, str]: ...
    def list(self) -> list[WorktreeSummary]: ...
    async def enter(self, name: str) -> str: ...
    async def exit(self, action: str, discard: bool) -> bool: ...
    async def remove(self, name: str, discard: bool) -> None: ...


class UI(Protocol):
    def println(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def mode(self) -> Mode: ...
    def set_mode(self, mode: Mode) -> None: ...
    async def inject_and_send(self, display_label: str, preset_prompt: str) -> None: ...
    def usage_in(self) -> int: ...
    def usage_out(self) -> int: ...
    def model_name(self) -> str: ...
    def cwd(self) -> str: ...
    def tool_count(self) -> int: ...
    def memory_files(self) -> list[str]: ...
    def session_path(self) -> str: ...
    def session_id(self) -> str: ...
    def hook_sources(self) -> list[str]: ...
    def hook_rules(self) -> list[HookRule]: ...
    def quit(self) -> None: ...
    async def force_compact(self) -> None: ...
    async def open_resume_menu(self) -> None: ...
    async def clear_and_new_session(self) -> None: ...
    async def end_session(self) -> None: ...
    def command_args(self) -> str: ...
    async def append_assistant_message(self, message: str, request: str = "") -> None: ...
    def idle(self) -> bool: ...
    def worktree_accessor(self) -> WorktreeAccessor | None: ...


class NopUI:
    """内置命令测试用的无副作用 UI。"""

    def println(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass

    def mode(self) -> Mode:
        return Mode.DEFAULT

    def set_mode(self, mode: Mode) -> None:
        pass

    async def inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        pass

    def usage_in(self) -> int:
        return 0

    def usage_out(self) -> int:
        return 0

    def model_name(self) -> str:
        return ""

    def cwd(self) -> str:
        return ""

    def tool_count(self) -> int:
        return 0

    def memory_files(self) -> list[str]:
        return []

    def session_path(self) -> str:
        return ""

    def session_id(self) -> str:
        return ""

    def hook_sources(self) -> list[str]:
        return []

    def hook_rules(self) -> list[HookRule]:
        return []

    def quit(self) -> None:
        pass

    async def force_compact(self) -> None:
        pass

    async def open_resume_menu(self) -> None:
        pass

    async def clear_and_new_session(self) -> None:
        pass

    async def end_session(self) -> None:
        pass

    def command_args(self) -> str:
        return ""

    async def append_assistant_message(self, message: str, request: str = "") -> None:
        pass

    def idle(self) -> bool:
        return True

    def worktree_accessor(self) -> WorktreeAccessor | None:
        return None
