"""iTerm2 Team 后端。"""

import asyncio
import contextlib
import shlex

from novacode.team.backend import SpawnRequest
from novacode.team.backend.tmux import build_member_cmd
from novacode.team.types import BackendType


async def _run(*args: str, ignore_error: bool = False) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0 and not ignore_error:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"it2 命令失败: {detail or process.returncode}")
    return stdout.decode(errors="replace").strip()


class Iterm2Backend:
    def type(self) -> BackendType:
        return BackendType.ITERM2

    async def spawn(self, request: SpawnRequest) -> tuple[str, str]:
        command = shlex.join(build_member_cmd(request))
        pane_id = await _run("it2", "split", "--new-pane", "--command", command)
        if not pane_id:
            raise RuntimeError("it2 未返回 pane id")
        return pane_id.splitlines()[-1], request.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        del agent_id
        if pane_id:
            await _run("it2", "send-text", "--pane", pane_id, "")

    async def kill(self, pane_id: str, agent_id: str) -> None:
        del agent_id
        if pane_id:
            with contextlib.suppress(Exception):
                await _run("it2", "close-pane", "--pane", pane_id, ignore_error=True)
