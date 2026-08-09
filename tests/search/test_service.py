from __future__ import annotations

import asyncio

import pytest

from novacode.runtime.errors import OperationCancelled, ValidationError
from novacode.search.domain import (
    ReadBudget,
    ReadRequest,
    SearchBudget,
    SearchKind,
    SearchRequest,
)
from novacode.search.service import FileSearchService


def test_search_budget_defaults_and_safety_caps() -> None:
    budget = SearchBudget()
    assert budget.max_results == 100
    assert budget.max_files == 20_000
    assert budget.max_file_bytes == 2 * 1024 * 1024
    assert budget.max_total_bytes == 64 * 1024 * 1024
    assert budget.timeout == 10.0

    with pytest.raises(ValidationError):
        SearchBudget(max_results=0)
    with pytest.raises(ValidationError):
        SearchBudget(max_total_bytes=1024 * 1024 * 1024)


@pytest.mark.asyncio
async def test_default_ignore_policy_excludes_generated_trees(isolated_state) -> None:
    (isolated_state.search_root / "keep.py").write_text("needle", encoding="utf-8")
    for directory in (".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"):
        ignored = isolated_state.search_root / directory
        ignored.mkdir()
        (ignored / "hidden.py").write_text("needle", encoding="utf-8")

    result = await FileSearchService().search(
        SearchRequest(SearchKind.GREP, str(isolated_state.search_root), "needle")
    )

    assert result.hits == ("keep.py:1:needle",)


@pytest.mark.asyncio
async def test_result_budget_returns_partial_hits_and_structured_reason(
    isolated_state,
) -> None:
    for index in range(10):
        (isolated_state.search_root / f"{index}.txt").write_text("hit", encoding="utf-8")

    result = await FileSearchService().search(
        SearchRequest(
            SearchKind.GREP,
            str(isolated_state.search_root),
            "hit",
            budget=SearchBudget(max_results=3),
        )
    )

    assert len(result.hits) == 3
    assert result.truncated is True
    assert result.reason == "result_limit"
    assert result.scanned_files <= 10
    assert result.scanned_bytes > 0


@pytest.mark.asyncio
async def test_search_runs_outside_event_loop(isolated_state) -> None:
    for index in range(600):
        (isolated_state.search_root / f"{index}.txt").write_text(
            "x" * 4096,
            encoding="utf-8",
        )
    service = FileSearchService()
    search = asyncio.create_task(
        service.search(
            SearchRequest(
                SearchKind.GREP,
                str(isolated_state.search_root),
                "not-present",
            )
        )
    )

    await asyncio.sleep(0)
    ticked_before_completion = not search.done()
    await search

    assert ticked_before_completion is True


@pytest.mark.asyncio
async def test_cancelled_search_stops_worker_within_grace(isolated_state) -> None:
    for index in range(1000):
        (isolated_state.search_root / f"{index}.txt").write_text(
            "x" * 8192,
            encoding="utf-8",
        )
    service = FileSearchService(cancellation_grace=1.0)
    search = asyncio.create_task(
        service.search(
            SearchRequest(
                SearchKind.GREP,
                str(isolated_state.search_root),
                "not-present",
            )
        )
    )
    await asyncio.sleep(0.01)
    search.cancel()

    with pytest.raises(OperationCancelled):
        await search
    assert service.active_workers == 0


@pytest.mark.asyncio
async def test_bounded_read_stops_before_allocating_complete_file(isolated_state) -> None:
    path = isolated_state.search_root / "large.txt"
    path.write_text("\n".join(f"line-{index}" for index in range(20_000)), encoding="utf-8")

    result = await FileSearchService().read(
        ReadRequest(
            str(path),
            budget=ReadBudget(max_lines=25, max_chars=512, max_bytes=1024),
        )
    )

    assert result.truncated is True
    assert result.lines <= 25
    assert len(result.content) <= 512
    assert result.bytes_read <= 1024
    assert path.stat().st_size > result.bytes_read


@pytest.mark.asyncio
async def test_sensitive_files_are_absent_from_hits_and_metadata(isolated_state) -> None:
    (isolated_state.search_root / ".env").write_text("API_KEY=secret", encoding="utf-8")
    (isolated_state.search_root / "safe.txt").write_text("API_KEY=public", encoding="utf-8")

    result = await FileSearchService().search(
        SearchRequest(SearchKind.GREP, str(isolated_state.search_root), "API_KEY")
    )
    diagnostic = repr(result)

    assert result.hits == ("safe.txt:1:API_KEY=public",)
    assert ".env" not in diagnostic
    assert "secret" not in diagnostic
