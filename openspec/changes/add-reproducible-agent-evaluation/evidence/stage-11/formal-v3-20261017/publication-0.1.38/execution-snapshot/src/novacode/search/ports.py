"""Search Service 的异步端口。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from novacode.search.domain import ReadRequest, ReadResult, SearchRequest, SearchResult


@runtime_checkable
class SearchService(Protocol):
    async def search(self, request: SearchRequest) -> SearchResult: ...

    async def read(self, request: ReadRequest) -> ReadResult: ...
