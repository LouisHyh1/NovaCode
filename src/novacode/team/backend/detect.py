"""按固定优先级检测 Agent Team 后端。"""

import os
import shutil

from novacode.team.types import BackendType


def detect() -> BackendType:
    if os.environ.get("TMUX"):
        return BackendType.TMUX
    if shutil.which("tmux"):
        return BackendType.TMUX
    return BackendType.IN_PROCESS
