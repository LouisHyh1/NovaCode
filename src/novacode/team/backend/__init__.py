"""Agent Team 执行后端抽象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from novacode.team.types import BackendType


@dataclass
class SpawnRequest:
    team_name: str
    member_name: str
    agent_id: str
    worktree_path: str
    session_dir: str
    agent_type: str
    model: str
    initial_prompt: str
    plan_mode_required: bool
    config_path: str = ""
    sub_agent: Any = None
    conv: Any = None
    task_mgr: Any = None


class Backend(Protocol):
    def type(self) -> BackendType: ...
    async def spawn(self, request: SpawnRequest) -> tuple[str, str]: ...
    async def wake(self, pane_id: str, agent_id: str) -> None: ...
    async def kill(self, pane_id: str, agent_id: str) -> None: ...


def new_backend(type_: BackendType, *, task_mgr=None) -> Backend:
    if type_ is BackendType.TMUX:
        from novacode.team.backend.tmux import TmuxBackend

        return TmuxBackend()
    if type_ is BackendType.ITERM2:
        from novacode.team.backend.iterm2 import Iterm2Backend

        return Iterm2Backend()
    from novacode.team.backend.inprocess import InProcessBackend

    if task_mgr is None:
        raise ValueError("in-process backend 缺少 TaskManager")
    return InProcessBackend(task_mgr)


from novacode.team.backend.detect import detect  # noqa: E402

__all__ = ["Backend", "SpawnRequest", "detect", "new_backend"]
