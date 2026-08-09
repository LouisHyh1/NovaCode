from dataclasses import replace

import pytest

from novacode.runtime.errors import DependencyCycleError, ValidationError
from novacode.team.domain import TeamId, TeamTask, TeamTaskGraph
from novacode.team.task_repository import JsonTeamTaskRepository


def _task(
    task_id: str,
    *,
    blocked_by: tuple[str, ...] = (),
    blocks: tuple[str, ...] = (),
    status: str = "pending",
) -> TeamTask:
    return TeamTask(
        task_id,
        task_id,
        status=status,
        blocked_by=frozenset(blocked_by),
        blocks=frozenset(blocks),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tasks", "error_type"),
    [
        ((_task("a", blocked_by=("missing",)),), ValidationError),
        ((_task("a", blocked_by=("a",), blocks=("a",)),), ValidationError),
        (
            (
                _task("a", blocked_by=("b",), blocks=("b",)),
                _task("b", blocked_by=("a",), blocks=("a",)),
            ),
            DependencyCycleError,
        ),
        (
            (
                _task("a", blocked_by=("c",), blocks=("b",)),
                _task("b", blocked_by=("a",), blocks=("c",)),
                _task("c", blocked_by=("b",), blocks=("a",)),
            ),
            DependencyCycleError,
        ),
    ],
)
async def test_invalid_dag_candidate_does_not_change_authoritative_bytes(
    isolated_state, tasks, error_type
) -> None:
    path = isolated_state.task_graph
    repository = JsonTeamTaskRepository(path)
    team_id = TeamId("team-1")
    await repository.create(TeamTaskGraph(team_id, (_task("root"),)))
    before = path.read_bytes()

    with pytest.raises(error_type):
        await repository.transact(team_id, lambda graph: replace(graph, tasks=tasks))

    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_valid_dependency_commits_both_edges_and_derives_readiness(
    isolated_state,
) -> None:
    repository = JsonTeamTaskRepository(isolated_state.task_graph)
    team_id = TeamId("team-1")
    blocker = _task("blocker", blocks=("blocked",))
    blocked = _task("blocked", blocked_by=("blocker",))
    await repository.create(TeamTaskGraph(team_id, (blocker, blocked)))

    pending = await repository.load(team_id)
    assert next(task for task in pending.tasks if task.task_id == "blocked").is_ready is False

    await repository.transact(
        team_id,
        lambda graph: replace(
            graph,
            tasks=tuple(
                replace(task, status="completed") if task.task_id == "blocker" else task
                for task in graph.tasks
            ),
        ),
    )
    completed = await repository.load(team_id)

    assert next(task for task in completed.tasks if task.task_id == "blocked").is_ready is True
    assert next(task for task in completed.tasks if task.task_id == "blocker").blocks == {"blocked"}
