"""Agent Team 生命周期管理。"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from novacode.runtime.errors import ConflictError, StateCorruptionError, ValidationError
from novacode.runtime.reports import (
    OperationReport,
    ResourceResult,
    ResourceStatus,
)
from novacode.team.backend import detect, new_backend
from novacode.team.domain import TeamTask
from novacode.team.mailbox import Box, Message
from novacode.team.persistence import read_json, sanitize, team_to_state
from novacode.team.repository import JsonTeamRepository
from novacode.team.types import (
    BackendType,
    MemberHasTasksError,
    MemberNotFoundError,
    Team,
    TeamError,
    TeamHasActiveMembersError,
    TeammateInfo,
    TeamNotFoundError,
)


@dataclass
class LeadMessage:
    team_name: str
    from_: str
    type: str
    content: str
    time: str


class Manager:
    def __init__(self, home_dir, project_root, wt_mgr, task_mgr, registry) -> None:
        self.home_dir = Path(home_dir)
        self.project_root = Path(project_root)
        self.wt_mgr = wt_mgr
        self.task_mgr = task_mgr
        self.run_registry = registry
        self.registry = registry  # 旧调用点兼容；Team 成员不写入该注册表。
        self.teams_dir = self.home_dir / ".novacode" / "teams"
        self.teams_dir.mkdir(parents=True, exist_ok=True)
        self.teams: dict[str, Team] = {}
        self.recovery_required: dict[str, StateCorruptionError] = {}
        self._lock = asyncio.Lock()
        self.catalog = None
        self.fork_teammate = False
        self._session_writers: dict[str, object] = {}
        self._load()

    def configure_spawn(self, catalog, *, fork_teammate: bool = False) -> None:
        self.catalog = catalog
        self.fork_teammate = fork_teammate

    def _load(self) -> None:
        for directory in sorted(self.teams_dir.iterdir()):
            if not directory.is_dir():
                continue
            try:
                team = Team.from_dict(read_json(directory / "config.json"), directory)
            except (OSError, TypeError, ValueError) as exc:
                error = StateCorruptionError(str(directory / "config.json"), detail=str(exc))
                self.recovery_required[str(directory)] = error
                print(
                    f"team: 跳过损坏配置 {error.path} (recovery-required)",
                    file=sys.stderr,
                )
                continue
            if not team.sanitized_name:
                error = StateCorruptionError(
                    str(directory / "config.json"),
                    detail="sanitized_name 缺失",
                )
                self.recovery_required[str(directory)] = error
                print(
                    f"team: 跳过损坏配置 {error.path} (recovery-required)",
                    file=sys.stderr,
                )
                continue
            self.teams[team.sanitized_name] = team
            for member in team.members:
                if member.backend_type is BackendType.IN_PROCESS and member.name != "lead":
                    member.is_active = False

    def get(self, name: str) -> Team | None:
        by_name = self.teams.get(sanitize(name))
        if by_name is not None:
            return by_name
        return next((team for team in self.teams.values() if team.team_id == name), None)

    def list(self) -> list[Team]:
        return sorted(self.teams.values(), key=lambda team: team.created_at)

    async def create(self, name: str, description: str = "") -> Team:
        sanitized = sanitize(name)
        if not sanitized:
            raise ValueError("Team 名称清理后为空")
        async with self._lock:
            candidate = sanitized
            suffix = 2
            while candidate in self.teams or (self.teams_dir / candidate).exists():
                candidate = f"{sanitized}-{suffix}"
                suffix += 1
            directory = self.teams_dir / candidate
            team = Team(
                name=name,
                sanitized_name=candidate,
                lead_agent_id="lead",
                backend=detect(),
                team_id=f"team-{uuid.uuid4().hex}",
                description=description,
                members=[TeammateInfo(name="lead", agent_id="lead")],
            )
            team.set_paths(directory)
            try:
                Path(team.mailbox_dir).mkdir(parents=True)
                state = await JsonTeamRepository(team.config_path).create(team_to_state(team))
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            from novacode.team.persistence import apply_team_state

            apply_team_state(team, state)
            self.teams[candidate] = team
            return team

    async def delete(self, name: str, force: bool = False) -> OperationReport:
        async with self._lock:
            team = self.get(name)
            if team is None:
                raise TeamNotFoundError(f"Team 不存在: {name}")
            active = [
                member.name
                for member in team.members
                if member.name != "lead" and member.is_active is not False
            ]
            if active and not force:
                raise TeamHasActiveMembersError(f"Team 仍有活跃成员: {', '.join(active)}")
            members = [member for member in team.members if member.name != "lead"]
        results: list[ResourceResult] = []
        for member in members:
            report = await self.remove_member(team.team_id, member.name, force=True)
            results.extend(
                replace(
                    result,
                    resource=f"member:{member.name}/{result.resource}",
                )
                for result in report.results
            )
        if any(result.status is ResourceStatus.FAILED for result in results):
            return OperationReport("delete-team", tuple(results))
        try:
            shutil.rmtree(team.config_dir)
        except OSError as exc:
            results.append(_cleanup_failure("team-directory", exc, team.config_dir))
            return OperationReport("delete-team", tuple(results))
        results.append(ResourceResult("team-directory", ResourceStatus.SUCCEEDED))
        async with self._lock:
            self.teams.pop(team.sanitized_name, None)
        return OperationReport("delete-team", tuple(results))

    async def _cleanup_member_resources(
        self,
        team: Team,
        member: TeammateInfo,
    ) -> tuple[ResourceResult, ...]:
        results: list[ResourceResult] = []
        if member.session_dir:
            writer = self._session_writers.pop(member.agent_id, None)
            if writer is not None:
                try:
                    writer.close()
                except (OSError, RuntimeError, ValueError) as exc:
                    results.append(_cleanup_failure("session-writer", exc, member.session_dir))
                else:
                    results.append(ResourceResult("session-writer", ResourceStatus.SUCCEEDED))
            path = Path(member.session_dir)
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            except OSError as exc:
                results.append(_cleanup_failure("session", exc, str(path)))
            else:
                results.append(ResourceResult("session", ResourceStatus.SUCCEEDED))
        if self.wt_mgr is not None and member.worktree_path:
            from novacode.worktree import ExitOptions

            slug = f"team-{team.sanitized_name}/{member.name}"
            try:
                await self.wt_mgr.remove(slug, ExitOptions(discard_changes=True))
            except (OSError, RuntimeError, ValueError) as exc:
                results.append(_cleanup_failure("worktree", exc, member.worktree_path))
            else:
                results.append(ResourceResult("worktree", ResourceStatus.SUCCEEDED))
        return tuple(results)

    async def remove_member(
        self,
        team_ref: str,
        member_name: str,
        *,
        force: bool = False,
    ) -> OperationReport:
        from novacode.team.domain import TeamId
        from novacode.team.task_repository import JsonTeamTaskRepository

        team = self.get(team_ref)
        if team is None:
            raise TeamNotFoundError(f"Team 不存在: {team_ref}")
        member = team.member_by_name(member_name)
        if member is None:
            raise MemberNotFoundError(f"Team 成员不存在: {member_name}")
        repository = JsonTeamTaskRepository(team.tasks_path)
        try:
            graph = await repository.load(TeamId(team.team_id))
        except KeyError:
            graph = None
        matching = (
            ()
            if graph is None
            else tuple(
                task
                for task in graph.tasks
                if str(task.assignee or "") in {member.agent_id, member.name}
            )
        )
        blockers = tuple(sorted(task.task_id for task in matching if task.status != "completed"))
        if blockers and not force:
            raise MemberHasTasksError(member_name, blockers)

        results: list[ResourceResult] = []
        if graph is not None and matching:
            try:
                await repository.transact(
                    TeamId(team.team_id),
                    lambda current: replace(
                        current,
                        tasks=tuple(
                            _detach_member_task(task, member, force) for task in current.tasks
                        ),
                    ),
                )
            except (StateCorruptionError, ConflictError, ValidationError) as exc:
                results.append(_operation_failure("team-task-graph", exc, team.tasks_path))
                return OperationReport("remove-member", tuple(results))
            results.append(ResourceResult("team-task-graph", ResourceStatus.SUCCEEDED))
        else:
            results.append(ResourceResult("team-task-graph", ResourceStatus.SKIPPED))

        try:
            await team.remove_member(member_name)
        except (TeamError, StateCorruptionError, ConflictError, ValidationError) as exc:
            results.append(_operation_failure("team-member", exc, team.config_path))
            return OperationReport("remove-member", tuple(results))
        results.append(ResourceResult("team-member", ResourceStatus.SUCCEEDED))
        backend = new_backend(member.backend_type, task_mgr=self.task_mgr)
        try:
            await backend.kill(member.pane_id, member.agent_id)
        except (OSError, RuntimeError, ValueError) as exc:
            results.append(_cleanup_failure("backend", exc, member.agent_id))
        else:
            results.append(ResourceResult("backend", ResourceStatus.SUCCEEDED))
        results.extend(await self._cleanup_member_resources(team, member))
        return OperationReport("remove-member", tuple(results))

    def team_for_agent(self, agent_id: str) -> tuple[Team, TeammateInfo] | None:
        for team in self.teams.values():
            member = team.member_by_agent_id(agent_id)
            if member is not None:
                return team, member
        return None

    def resolve_member(self, team_ref: str, name_or_id: str) -> TeammateInfo | None:
        team = self.get(team_ref)
        if team is None:
            return None
        return team.member_by_name(name_or_id) or team.member_by_agent_id(name_or_id)

    async def handle_task_done(self, agent_id: str) -> None:
        found = self.team_for_agent(agent_id)
        if found is None:
            return
        team, member = found
        if member.name == "lead":
            return
        with contextlib.suppress(Exception):
            await team.set_member_active(member.name, False)
        await Box(team.mailbox_dir).write(
            team.lead_agent_id,
            Message(from_=member.name, text=f"[idle] {member.name} (reason: available)"),
        )

    async def poll_lead_mailboxes(self) -> list[LeadMessage]:
        result: list[LeadMessage] = []
        for team in self.list():
            box = Box(team.mailbox_dir)
            indices, messages = await box.read_unread(team.lead_agent_id)
            for message in messages:
                result.append(
                    LeadMessage(
                        team_name=team.sanitized_name,
                        from_=message.from_,
                        type=message.type.value,
                        content=message.text,
                        time=message.timestamp,
                    )
                )
            await box.mark_read(team.lead_agent_id, indices)
        return result

    async def spawn_teammate(self, request) -> str:
        """实现 Agent 工具的 team_name 分支。"""
        import json

        from novacode.agent import Agent
        from novacode.agent.fork import build_forked_messages
        from novacode.agent.team_hook import TeammateContext
        from novacode.conversation import Conversation
        from novacode.permission import Mode
        from novacode.session import SessionWriter
        from novacode.team.backend import SpawnRequest
        from novacode.team.mailbox import Message
        from novacode.tool.filter import FilterParams, apply_agent_tool_filter

        team = self.get(request.team_name)
        if team is None:
            raise TeamNotFoundError(f"Team 不存在: {request.team_name}")
        caller_context = getattr(request.caller_agent, "teammate_context", None)
        if caller_context is not None:
            raise RuntimeError("Team 队员不能继续向 Team 添加成员")
        if self.catalog is None:
            raise RuntimeError("Team Manager 尚未配置 SubAgent Catalog")
        member_name = request.member_name.strip() or f"agent-{uuid.uuid4().hex[:7]}"
        if team.member_by_name(member_name) is not None:
            raise RuntimeError(f"Team 成员已存在: {member_name}")
        if self.wt_mgr is None:
            raise RuntimeError("Worktree 管理器不可用")

        if request.subagent_type:
            definition = self.catalog.resolve(request.subagent_type)
        elif self.fork_teammate:
            definition = self.catalog.fork_definition()
        else:
            definition = self.catalog.resolve("general-purpose")
        if definition is None:
            raise RuntimeError(f"未知 subagent_type: {request.subagent_type}")

        slug = f"team-{team.sanitized_name}/{member_name}"
        worktree = await self.wt_mgr.create(slug, "HEAD", False)
        agent_id = f"agent-{uuid.uuid4().hex[:14]}"
        sessions_dir = self.project_root / ".novacode" / "sessions"
        session_id = f"team-{team.sanitized_name}-{member_name}-{uuid.uuid4().hex[:8]}"
        session_path = sessions_dir / f"{session_id}.jsonl"
        box = Box(team.mailbox_dir)
        teammate_context = TeammateContext(
            team_name=team.sanitized_name,
            member_name=member_name,
            agent_id=agent_id,
            backend_type=team.backend.value,
            mailbox=box,
            team_manager=self,
            team_id=team.team_id,
        )
        all_names = [item.name for item in request.caller_agent.registry.definitions()]
        allowed = apply_agent_tool_filter(
            FilterParams(
                all=all_names,
                source=int(definition.source),
                background=False,
                allowed=definition.tools,
                disallowed=definition.disallowed_tools,
                fork=definition.is_fork(),
                teammate=True,
            )
        )
        suffix = (
            "IMPORTANT: You are running as an agent in a team.\n"
            "Just writing a response in text is not visible to others on your team - "
            "you MUST use the SendMessage tool.\n"
            "The user interacts primarily with the team lead. Your work is coordinated "
            "through the task system and teammate messaging."
        )
        system_prompt = None if definition.is_fork() else definition.system_prompt
        if system_prompt:
            system_prompt = f"{system_prompt}\n\n{suffix}"
        else:
            system_prompt = suffix
        writer = None
        try:
            writer = SessionWriter(sessions_dir, session_id, request.caller_agent.provider.model)
            if definition.is_fork():
                messages = build_forked_messages(
                    request.caller_conversation.messages(), request.prompt
                )
                conversation = Conversation.from_messages(
                    messages,
                    writer.append_message,
                    writer.append_compaction,
                )
                initial_prompt = ""
            else:
                conversation = Conversation(writer.append_message, writer.append_compaction)
                initial_prompt = request.prompt
            permission_mode = (
                Mode.PLAN if request.plan_mode_required else definition.permission_mode
            )
            sub_agent = Agent(
                request.caller_agent.provider,
                request.caller_agent.registry,
                request.caller_agent.version,
                request.caller_agent.engine,
                context_window=request.caller_agent.context_window,
                instructions=request.caller_agent.instructions,
                memory_index=request.caller_agent.memory_index,
                hook_engine=request.caller_agent.hook_engine,
                system_prompt=system_prompt,
                max_turns=definition.max_turns,
                permission_mode=permission_mode,
                dont_ask=True,
                allowed_tools=allowed,
                subagent_name=definition.name,
                teammate_context=teammate_context,
            )
            context_members = ", ".join(
                f"{member.name}({'lead' if member.name == 'lead' else 'teammate'})"
                for member in team.members
            )
            sub_agent.runtime.append_reminders(
                [
                    "<team-context>\n"
                    f"team: {team.sanitized_name}\n"
                    f"team_id: {team.team_id}\n"
                    f"你的成员名: {member_name}\n"
                    f"你的 agent_id: {agent_id}\n"
                    f"worktree 目录: {worktree.path}\n"
                    f"当前团队成员: {context_members}\n"
                    "</team-context>"
                ]
            )
            info = TeammateInfo(
                name=member_name,
                agent_id=agent_id,
                agent_type=request.subagent_type,
                model=request.model,
                worktree_path=worktree.path,
                branch=worktree.branch,
                backend_type=team.backend,
                is_active=True,
                plan_mode_required=request.plan_mode_required,
                session_dir=str(session_path),
            )
            await team.add_member(info)
            if team.backend is not BackendType.IN_PROCESS:
                await box.write(agent_id, Message(from_="lead", text=request.prompt))
                writer.close()
            backend = new_backend(team.backend, task_mgr=self.task_mgr)
            pane_id, spawned_id = await backend.spawn(
                SpawnRequest(
                    team_name=team.sanitized_name,
                    member_name=member_name,
                    agent_id=agent_id,
                    worktree_path=worktree.path,
                    session_dir=str(session_path),
                    agent_type=request.subagent_type,
                    model=request.model,
                    initial_prompt=initial_prompt,
                    plan_mode_required=request.plan_mode_required,
                    config_path=str(self.project_root / ".novacode" / "config.yaml"),
                    sub_agent=sub_agent,
                    conv=conversation,
                    task_mgr=self.task_mgr,
                )
            )
            await team.update_member_identity(
                member_name,
                agent_id=spawned_id,
                pane_id=pane_id,
            )
            if team.backend is BackendType.IN_PROCESS:
                self._session_writers[spawned_id] = writer
            return json.dumps(
                {
                    "member_name": member_name,
                    "agent_id": spawned_id,
                    "worktree": worktree.path,
                    "backend": team.backend.value,
                    "pane_id": pane_id,
                },
                ensure_ascii=False,
            )
        except Exception:
            if writer is not None:
                with contextlib.suppress(Exception):
                    writer.close()
            with contextlib.suppress(Exception):
                await team.remove_member(member_name)
            from novacode.worktree import ExitOptions

            with contextlib.suppress(Exception):
                await self.wt_mgr.remove(slug, ExitOptions(discard_changes=True))
            raise


def _detach_member_task(
    task: TeamTask,
    member: TeammateInfo,
    force: bool,
) -> TeamTask:
    if str(task.assignee or "") not in {member.agent_id, member.name}:
        return task
    if task.status == "completed":
        return replace(
            task,
            assignee=None,
            historical_assignee=member.name,
        )
    if force:
        return replace(task, assignee=None)
    return task


def _cleanup_failure(
    resource: str,
    error: BaseException,
    residual_path: str,
) -> ResourceResult:
    return ResourceResult(
        resource,
        ResourceStatus.FAILED,
        error_category="cleanup",
        message=str(error),
        residual_path=residual_path,
    )


def _operation_failure(
    resource: str,
    error: BaseException,
    residual_path: str,
) -> ResourceResult:
    category = str(getattr(error, "category", type(error).__name__))
    return ResourceResult(
        resource,
        ResourceStatus.FAILED,
        error_category=category,
        message=str(error),
        residual_path=residual_path,
    )
