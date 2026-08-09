from __future__ import annotations

import pytest

from novacode.runtime.errors import ConflictError
from novacode.runtime.reports import OperationStatus
from novacode.team import BackendType, Manager, MemberHasTasksError, TeammateInfo
from novacode.team.domain import AgentId, TeamId, TeamTask, TeamTaskGraph
from novacode.team.registry import AgentRunRegistry
from novacode.team.task_repository import JsonTeamTaskRepository


class FakeTaskManager:
    async def stop(self, _agent_id: str) -> bool:
        return True


class FakeWorktreeManager:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def remove(self, _name, _options) -> None:
        if self.fail:
            raise OSError("worktree cleanup failed")


async def _manager_with_assigned_tasks(tmp_path, monkeypatch, *, cleanup_fails=False):
    monkeypatch.setattr("novacode.team.manager.detect", lambda: BackendType.IN_PROCESS)
    project = tmp_path / "project"
    project.mkdir()
    manager = Manager(
        tmp_path / "home",
        project,
        FakeWorktreeManager(fail=cleanup_fails),
        FakeTaskManager(),
        AgentRunRegistry(),
    )
    team = await manager.create("demo")
    await team.add_member(
        TeammateInfo(
            "alice",
            "agent-a",
            worktree_path=str(project / "alice-worktree"),
            is_active=False,
        )
    )
    repository = JsonTeamTaskRepository(team.tasks_path)
    await repository.create(
        TeamTaskGraph(
            TeamId(team.team_id),
            (
                TeamTask(
                    "pending",
                    "pending",
                    assignee=AgentId("agent-a"),
                ),
                TeamTask(
                    "completed",
                    "completed",
                    status="completed",
                    assignee=AgentId("agent-a"),
                ),
            ),
        )
    )
    return manager, team, repository


@pytest.mark.asyncio
async def test_normal_member_removal_reports_all_incomplete_task_blockers(
    tmp_path, monkeypatch
) -> None:
    manager, team, _repository = await _manager_with_assigned_tasks(tmp_path, monkeypatch)
    before = await JsonTeamTaskRepository(team.tasks_path).load(TeamId(team.team_id))

    with pytest.raises(MemberHasTasksError) as captured:
        await manager.remove_member(team.team_id, "alice")

    after = await JsonTeamTaskRepository(team.tasks_path).load(TeamId(team.team_id))
    assert captured.value.task_ids == ("pending",)
    assert after == before
    assert team.member_by_name("alice") is not None


@pytest.mark.asyncio
async def test_forced_removal_unassigns_tasks_and_preserves_history(tmp_path, monkeypatch) -> None:
    manager, team, repository = await _manager_with_assigned_tasks(tmp_path, monkeypatch)

    report = await manager.remove_member(team.team_id, "alice", force=True)
    graph = await repository.load(TeamId(team.team_id))
    pending = next(task for task in graph.tasks if task.task_id == "pending")
    completed = next(task for task in graph.tasks if task.task_id == "completed")

    assert report.status is OperationStatus.COMPLETED
    assert pending.assignee is None
    assert completed.assignee is None
    assert completed.historical_assignee == "alice"
    assert team.member_by_name("alice") is None


@pytest.mark.asyncio
async def test_member_delete_failure_leaves_retry_safe_unbound_tasks(tmp_path, monkeypatch) -> None:
    manager, team, repository = await _manager_with_assigned_tasks(tmp_path, monkeypatch)

    async def fail_remove(_name: str) -> None:
        raise ConflictError("injected member delete failure")

    monkeypatch.setattr(team, "remove_member", fail_remove)
    report = await manager.remove_member(team.team_id, "alice", force=True)
    graph = await repository.load(TeamId(team.team_id))

    assert report.status is OperationStatus.INCOMPLETE
    assert team.member_by_name("alice") is not None
    assert all(task.assignee is None for task in graph.tasks)


@pytest.mark.asyncio
async def test_cleanup_failure_is_reported_with_residual_path(tmp_path, monkeypatch) -> None:
    manager, team, _repository = await _manager_with_assigned_tasks(
        tmp_path,
        monkeypatch,
        cleanup_fails=True,
    )

    report = await manager.remove_member(team.team_id, "alice", force=True)

    assert report.status is OperationStatus.INCOMPLETE
    assert any(
        result.resource == "worktree"
        and result.error_category == "cleanup"
        and result.residual_path.endswith("alice-worktree")
        for result in report.results
    )
