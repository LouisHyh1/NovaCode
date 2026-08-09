from novacode.team.ports import (
    MailboxRepository,
    TeamRepository,
    TeamTaskRepository,
)


class StructurallyCompatibleRepositories:
    async def load(self, aggregate_id: str) -> object:
        return object()

    async def create(self, state: object) -> object:
        return state

    async def transact(self, aggregate_id: str, mutation: object) -> object:
        return object()

    async def append(self, agent_id: object, message: object) -> None:
        return None

    async def list_messages(self, agent_id: object) -> tuple[object, ...]:
        return ()


def test_repository_ports_are_async_structural_contracts() -> None:
    repository = StructurallyCompatibleRepositories()

    assert isinstance(repository, TeamRepository)
    assert isinstance(repository, TeamTaskRepository)
    assert isinstance(repository, MailboxRepository)
