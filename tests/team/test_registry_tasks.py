import re

import pytest

from novacode.team.registry import AgentNameRegistry
from novacode.team.tasks import Filter, Patch, Status, Store, Task


def test_registry_bidirectional_and_overwrite() -> None:
    registry = AgentNameRegistry()
    registry.register("alice", "agent-1")
    assert registry.resolve("alice") == "agent-1"
    assert registry.resolve("agent-1") == "agent-1"
    assert registry.name_of("agent-1") == "alice"
    registry.register("alice", "agent-2")
    assert registry.resolve("agent-1") is None
    registry.register("bob", "agent-2")
    assert registry.resolve("alice") is None
    assert registry.name_of("agent-2") == "bob"


@pytest.mark.asyncio
async def test_task_store_crud_dependencies_and_readiness(tmp_path) -> None:
    store = Store(tmp_path / "tasks.json")
    blocker = await store.create(Task(title="blocker"))
    blocked = await store.create(Task(title="blocked"))
    assert re.fullmatch(r"task_[0-9a-f]{6}", blocker)
    await store.update(blocked, Patch(add_blocked_by=[blocker]))
    assert blocked in (await store.get(blocker)).blocks
    pending = await store.list(Filter(Status.PENDING))
    assert next(task for task in pending if task.id == blocked).is_ready is False
    await store.update(blocker, Patch(status=Status.COMPLETED))
    all_tasks = await store.list()
    assert next(task for task in all_tasks if task.id == blocked).is_ready is True
