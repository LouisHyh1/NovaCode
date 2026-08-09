from novacode.search.ports import SearchService


class StructurallyCompatibleSearch:
    async def search(self, request: object) -> object:
        return object()

    async def read(self, request: object) -> object:
        return object()


def test_search_service_is_a_structural_async_contract() -> None:
    assert isinstance(StructurallyCompatibleSearch(), SearchService)
