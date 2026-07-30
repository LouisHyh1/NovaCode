"""tmux Team 后端。"""

import asyncio
import contextlib
import os
import sys

from novacode.team.backend import SpawnRequest
from novacode.team.types import BackendType


def build_member_cmd(request: SpawnRequest) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "novacode",
        "--team-member",
        "--team",
        request.team_name,
        "--member",
        request.member_name,
        "--agent-id",
        request.agent_id,
        "--session-dir",
        request.session_dir,
        "--worktree",
        request.worktree_path,
    ]
    if request.agent_type:
        command.extend(("--agent-type", request.agent_type))
    if request.model:
        command.extend(("--model", request.model))
    if request.plan_mode_required:
        command.append("--plan-mode")
    if request.config_path:
        command.extend(("--config", request.config_path))
    return command


async def _run(*args: str, ignore_error: bool = False) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0 and not ignore_error:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"tmux 命令失败: {detail or process.returncode}")
    return stdout.decode(errors="replace").strip()


class TmuxBackend:
    def type(self) -> BackendType:
        return BackendType.TMUX

    async def spawn(self, request: SpawnRequest) -> tuple[str, str]:
        command = build_member_cmd(request)
        if os.environ.get("TMUX"):
            pane_id = await _run(
                "tmux", "split-window", "-h", "-P", "-F", "#{pane_id}", "--", *command
            )
        else:
            pane_id = await _run("tmux", "new-session", "-d", "-P", "-F", "#{pane_id}", *command)
        if not pane_id:
            raise RuntimeError("tmux 未返回 pane id")
        return pane_id.splitlines()[-1], request.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        del agent_id
        if pane_id:
            await _run("tmux", "send-keys", "-t", pane_id, "", "Enter")

    async def kill(self, pane_id: str, agent_id: str) -> None:
        del agent_id
        if pane_id:
            with contextlib.suppress(Exception):
                await _run("tmux", "kill-pane", "-t", pane_id, ignore_error=True)
