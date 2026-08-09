from dataclasses import replace

import pytest

from novacode.runtime.errors import ConflictError
from novacode.team.domain import (
    AgentAddress,
    AgentId,
    MemberName,
    TeamId,
    TeamMember,
    TeamState,
    TeamTask,
    TeamTaskGraph,
)
from novacode.team.repository import JsonTeamRepository
from novacode.team.task_repository import JsonTeamTaskRepository


@pytest.mark.asyncio
async def test_team_repository_updates_one_versioned_aggregate(isolated_state) -> None:
    path = isolated_state.team / "config.json"
    repository = JsonTeamRepository(path)
    team_id = TeamId("team-1")
    alice = TeamMember(
        AgentAddress(team_id, MemberName("alice")),
        AgentId("agent-a"),
        active=True,
    )
    bob = TeamMember(
        AgentAddress(team_id, MemberName("bob")),
        AgentId("agent-b"),
        active=False,
    )

    created = await repository.create(TeamState(team_id, "demo", (alice,)))
    updated = await repository.transact(
        team_id,
        lambda current: replace(current, members=(*current.members, bob)),
    )
    loaded = await repository.load(team_id)

    assert created.revision == 1
    assert updated.revision == 2
    assert loaded == updated
    assert [member.address.member_name for member in loaded.members] == ["alice", "bob"]


@pytest.mark.asyncio
async def test_team_repository_rejects_duplicate_create(isolated_state) -> None:
    repository = JsonTeamRepository(isolated_state.team / "config.json")
    state = TeamState(TeamId("team-1"), "demo")
    await repository.create(state)

    with pytest.raises(ConflictError):
        await repository.create(state)


@pytest.mark.asyncio
async def test_team_task_repository_publishes_complete_versioned_graph(isolated_state) -> None:
    team_id = TeamId("team-1")
    repository = JsonTeamTaskRepository(isolated_state.task_graph)
    first = TeamTask("task-1", "first")
    second = TeamTask("task-2", "second", blocked_by=frozenset({"task-1"}))

    created = await repository.create(TeamTaskGraph(team_id, (first,)))
    updated = await repository.transact(
        team_id,
        lambda current: replace(
            current,
            tasks=(
                replace(current.tasks[0], blocks=frozenset({"task-2"})),
                second,
            ),
        ),
    )
    loaded = await repository.load(team_id)

    assert created.revision == 1
    assert updated.revision == 2
    assert loaded == updated
    assert {task.task_id for task in loaded.tasks} == {"task-1", "task-2"}
