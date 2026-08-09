import pytest

from novacode.team.domain import (
    AgentAddress,
    AgentId,
    AgentRun,
    MemberName,
    TeamId,
    TeamMemberDirectory,
    TeamTask,
)


def test_member_names_are_unique_only_inside_their_team() -> None:
    first = TeamMemberDirectory(TeamId("team-1"))
    second = TeamMemberDirectory(TeamId("team-2"))

    first_address = first.register(MemberName("alice"), AgentId("agent-1"))
    second_address = second.register(MemberName("alice"), AgentId("agent-2"))

    assert first_address == AgentAddress(TeamId("team-1"), MemberName("alice"))
    assert second_address == AgentAddress(TeamId("team-2"), MemberName("alice"))
    assert first.resolve(MemberName("alice")) == AgentId("agent-1")
    assert second.resolve(MemberName("alice")) == AgentId("agent-2")
    with pytest.raises(ValueError, match="Team 成员名已存在"):
        first.register(MemberName("alice"), AgentId("agent-3"))


def test_agent_run_and_team_task_are_distinct_types() -> None:
    run = AgentRun(run_id="run-1", agent_id=AgentId("agent-1"), name="explore")
    task = TeamTask(task_id="task-1", title="实现搜索")

    assert run.run_id == "run-1"
    assert task.task_id == "task-1"
    assert type(run) is not type(task)
