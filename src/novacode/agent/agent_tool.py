"""主 Agent 用于启动 SubAgent 的统一工具。"""

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from novacode.agent import Agent
from novacode.agent.context import current
from novacode.agent.fork import build_forked_messages, is_fork_context
from novacode.conversation import Conversation
from novacode.subagent import Catalog, Definition
from novacode.task import (
    AgentRunManager as Manager,
)
from novacode.task import (
    AgentRunPartialState as PartialState,
)
from novacode.tool import Result
from novacode.tool.filter import FilterParams, apply_agent_tool_filter

if TYPE_CHECKING:
    from novacode.agent.team_hook import TeamHook
    from novacode.worktree import Manager as WorktreeManager

AUTO_BACKGROUND_SECONDS = 120.0


@dataclass
class AgentArgs:
    prompt: str
    description: str
    subagent_type: str = ""
    isolation: str = ""
    model: str = ""
    run_in_background: bool = False
    name: str = ""
    team_name: str = ""
    plan_mode_required: bool = False


class AgentTool:
    read_only = False

    @property
    def timeout(self) -> float:
        """外层 Registry 超时必须晚于前台自动转后台计时。"""
        return AUTO_BACKGROUND_SECONDS + 5.0

    def __init__(
        self,
        catalog: Catalog,
        task_mgr: Manager,
        parent: Agent | None = None,
        bg_enabled: bool = True,
        worktree_mgr: "WorktreeManager | None" = None,
        team_hook: "TeamHook | None" = None,
    ) -> None:
        self.catalog = catalog
        self.task_mgr = task_mgr
        self.parent = parent
        self.bg_enabled = bg_enabled
        self.worktree_mgr = worktree_mgr
        self.team_hook = team_hook

    def name(self) -> str:
        return "Agent"

    def description(self) -> str:
        names = ", ".join(definition.name for definition in self.catalog.list())
        return (
            "启动独立 SubAgent 完成任务。指定 subagent_type 使用预定义角色；"
            f"留空会 Fork 当前对话并在后台运行。可选角色: {names}。"
            "提供 team_name 时在该 Team 的独立 Worktree 中启动长期队员。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "description": {"type": "string"},
                "subagent_type": {"type": "string"},
                "isolation": {
                    "type": "string",
                    "enum": ["worktree"],
                    "description": "在独立 Git Worktree 中执行 SubAgent",
                },
                "model": {
                    "type": "string",
                    "enum": ["haiku", "sonnet", "opus", "inherit"],
                },
                "run_in_background": {"type": "boolean"},
                "name": {"type": "string"},
                "team_name": {"type": "string"},
                "plan_mode_required": {"type": "boolean"},
            },
            "required": ["prompt", "description"],
            "additionalProperties": False,
        }

    def set_parent(self, agent: Agent) -> None:
        self.parent = agent

    @staticmethod
    def _parse_args(raw: str) -> AgentArgs | Result:
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            return Result(f"Agent 参数不是合法 JSON: {exc}", is_error=True)
        if not isinstance(data, dict):
            return Result("Agent 参数必须是对象", is_error=True)
        prompt = data.get("prompt")
        description = data.get("description")
        if not isinstance(prompt, str) or not prompt.strip():
            return Result("prompt is required", is_error=True)
        if not isinstance(description, str) or not description.strip():
            return Result("description is required", is_error=True)
        isolation = str(data.get("isolation") or "")
        if isolation not in {"", "worktree"}:
            return Result("isolation 仅支持 worktree", is_error=True)
        return AgentArgs(
            prompt=prompt,
            description=description,
            subagent_type=str(data.get("subagent_type") or ""),
            isolation=isolation,
            model=str(data.get("model") or ""),
            run_in_background=data.get("run_in_background") is True,
            name=str(data.get("name") or ""),
            team_name=str(data.get("team_name") or ""),
            plan_mode_required=data.get("plan_mode_required") is True,
        )

    def _new_agent(self, definition: Definition, background: bool) -> Agent:
        assert self.parent is not None
        all_names = [item.name for item in self.parent.registry.definitions()]
        allowed = apply_agent_tool_filter(
            FilterParams(
                all=all_names,
                source=int(definition.source),
                background=background,
                allowed=definition.tools,
                disallowed=definition.disallowed_tools,
                fork=definition.is_fork(),
            )
        )
        return Agent(
            self.parent.provider,
            self.parent.registry,
            self.parent.version,
            self.parent.engine,
            context_window=self.parent.context_window,
            instructions=self.parent.instructions,
            memory_index=self.parent.memory_index,
            hook_engine=self.parent.hook_engine,
            system_prompt=None if definition.is_fork() else definition.system_prompt,
            max_turns=definition.max_turns,
            permission_mode=definition.permission_mode,
            dont_ask=definition.dont_ask,
            approval_upgrader=self.task_mgr.upgrade_approval,
            allowed_tools=allowed,
            subagent_name=definition.name,
        )

    async def execute(self, args: str) -> Result:
        parsed = self._parse_args(args)
        if isinstance(parsed, Result):
            return parsed
        if self.parent is None:
            return Result("Agent 工具尚未绑定主 Agent", is_error=True)

        caller = current()
        if parsed.team_name:
            if self.team_hook is None:
                return Result("Team Manager 未配置", is_error=True)
            if caller is not None and getattr(caller.agent, "teammate_context", None) is not None:
                return Result("Team 队员不能继续向 Team 添加成员", is_error=True)
            from novacode.agent.team_hook import TeamSpawnRequest

            try:
                content = await self.team_hook.spawn_teammate(
                    TeamSpawnRequest(
                        team_name=parsed.team_name,
                        member_name=parsed.name,
                        prompt=parsed.prompt,
                        description=parsed.description,
                        subagent_type=parsed.subagent_type,
                        model=parsed.model,
                        plan_mode_required=parsed.plan_mode_required,
                        caller_agent=self.parent,
                        caller_conversation=(
                            caller.conversation if caller is not None else Conversation()
                        ),
                    )
                )
            except Exception as exc:
                return Result(f"Team 队员启动失败: {exc}", is_error=True)
            return Result(content)
        if caller is not None and is_fork_context(caller.conversation.messages()):
            return Result("Fork 子 Agent 不能再启动 Agent", is_error=True)
        if caller is not None and caller.agent.subagent_name:
            return Result("SubAgent 不能再启动 Agent", is_error=True)

        if parsed.subagent_type:
            definition = self.catalog.resolve(parsed.subagent_type)
            if definition is None:
                return Result(f"未知 subagent_type: {parsed.subagent_type}", is_error=True)
        else:
            definition = self.catalog.fork_definition()

        isolated = parsed.isolation == "worktree" or definition.isolation == "worktree"
        if isolated and self.worktree_mgr is None:
            return Result("Worktree 管理器未配置，无法启动隔离 SubAgent", is_error=True)

        background = definition.background or parsed.run_in_background or definition.is_fork()
        if isolated:
            # ch14 最小实现：隔离 SubAgent 强制前台，确保生命周期与清理完整结束。
            background = False
        if background and not self.bg_enabled:
            return Result("后台禁用，无法 Fork 或启动后台 SubAgent", is_error=True)

        sub_agent = self._new_agent(definition, background)
        if definition.is_fork():
            parent_messages = caller.conversation.messages() if caller is not None else []
            conversation = Conversation.from_messages(
                build_forked_messages(parent_messages, parsed.prompt)
            )
        else:
            conversation = Conversation()

        if background:
            task_id = await self.task_mgr.launch(
                sub_agent, conversation, parsed.name, parsed.prompt
            )
            return Result(json.dumps({"task_id": task_id, "status": "async_launched"}))

        events: asyncio.Queue = asyncio.Queue()
        if isolated:
            from novacode.agent.agent_worktree import execute_with_worktree

            assert self.worktree_mgr is not None
            coroutine = execute_with_worktree(
                self.worktree_mgr,
                sub_agent,
                conversation,
                parsed.prompt,
                events,
            )
        else:
            coroutine = sub_agent.run_to_completion(conversation, parsed.prompt, events)
        handle = asyncio.create_task(coroutine)
        try:
            final_text = await asyncio.wait_for(
                asyncio.shield(handle), timeout=AUTO_BACKGROUND_SECONDS
            )
        except TimeoutError:
            if not self.bg_enabled:
                handle.cancel()
                return Result("SubAgent 前台执行超时", is_error=True)
            task_id = await self.task_mgr.adopt_running(
                sub_agent,
                conversation,
                parsed.name,
                events,
                handle,
                PartialState(),
                parsed.prompt,
            )
            return Result(json.dumps({"task_id": task_id, "status": "timed_out_to_background"}))
        except asyncio.CancelledError:
            handle.cancel()
            try:
                await handle
            except asyncio.CancelledError:
                pass
            raise
        except Exception as exc:
            return Result(f"SubAgent error: {exc}", is_error=True)
        return Result(final_text)
