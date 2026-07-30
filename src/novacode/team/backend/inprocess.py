"""同进程 asyncio Team 后端。"""

from novacode.team.backend import SpawnRequest
from novacode.team.types import BackendType


class InProcessBackend:
    def __init__(self, task_mgr) -> None:
        self.task_mgr = task_mgr

    def type(self) -> BackendType:
        return BackendType.IN_PROCESS

    async def spawn(self, request: SpawnRequest) -> tuple[str, str]:
        if request.sub_agent is None or request.conv is None:
            raise ValueError("in-process spawn 缺少 Agent 或 Conversation")
        task_id = await self.task_mgr.launch(
            request.sub_agent,
            request.conv,
            request.member_name,
            request.initial_prompt,
            task_id=request.agent_id,
            cwd=request.worktree_path,
        )
        return "", task_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        return None

    async def kill(self, pane_id: str, agent_id: str) -> None:
        await self.task_mgr.stop(agent_id)
