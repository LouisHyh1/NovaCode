import asyncio
import json

import pytest

from novacode.search.domain import ReadBudget, SearchBudget
from novacode.search.service import FileSearchService
from novacode.tool.glob_tool import GlobTool
from novacode.tool.grep_tool import GrepTool
from novacode.tool.read_file import ReadFileTool


@pytest.mark.asyncio
async def test_glob_tool_exposes_truncation_metadata(tmp_path) -> None:
    for index in range(5):
        (tmp_path / f"{index}.py").write_text("", encoding="utf-8")
    tool = GlobTool(FileSearchService(default_budget=SearchBudget(max_results=2)))

    result = await tool.execute(json.dumps({"pattern": "*.py", "path": str(tmp_path)}))

    assert result.is_error is False
    assert len([line for line in result.content.splitlines() if line.endswith(".py")]) == 2
    assert result.metadata["truncated"] is True
    assert result.metadata["reason"] == "result_limit"


@pytest.mark.asyncio
async def test_grep_tool_preserves_display_and_structured_counts(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    tool = GrepTool(FileSearchService())

    result = await tool.execute(json.dumps({"pattern": "needle", "path": str(tmp_path)}))

    assert "a.txt:1:needle" in result.content
    assert result.metadata["scanned_files"] == 1
    assert result.metadata["scanned_bytes"] > 0


@pytest.mark.asyncio
async def test_read_file_tool_reports_bounded_prefix(tmp_path) -> None:
    path = tmp_path / "large.txt"
    path.write_text("\n".join(str(index) for index in range(100)), encoding="utf-8")
    tool = ReadFileTool(
        FileSearchService(default_read_budget=ReadBudget(max_lines=3, max_chars=100, max_bytes=100))
    )

    result = await tool.execute(json.dumps({"path": str(path)}))

    assert result.content.splitlines()[0].startswith("     1\t")
    assert result.metadata["truncated"] is True
    assert result.metadata["lines"] <= 3


@pytest.mark.asyncio
async def test_grep_tool_large_directory_no_match_is_bounded(tmp_path) -> None:
    for index in range(200):
        (tmp_path / f"{index}.txt").write_text("ordinary text", encoding="utf-8")
    tool = GrepTool(FileSearchService(default_budget=SearchBudget(max_files=50, timeout=10.0)))

    result = await tool.execute(json.dumps({"pattern": "not-present", "path": str(tmp_path)}))

    assert result.is_error is False
    assert result.metadata["truncated"] is True
    assert result.metadata["reason"] == "file_limit"
    assert result.metadata["scanned_files"] == 50


@pytest.mark.asyncio
async def test_glob_tool_timeout_exposes_partial_result_reason(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / "match.py").write_text("", encoding="utf-8")
    tick = -1

    def monotonic() -> float:
        nonlocal tick
        tick += 1
        return float(tick)

    monkeypatch.setattr("novacode.search.service.time.monotonic", monotonic)
    tool = GlobTool(FileSearchService(default_budget=SearchBudget(timeout=0.5)))

    result = await tool.execute(json.dumps({"pattern": "*.py", "path": str(tmp_path)}))

    assert result.is_error is False
    assert result.metadata["truncated"] is True
    assert result.metadata["reason"] == "timeout"


@pytest.mark.asyncio
async def test_cancelled_grep_tool_finishes_worker_within_grace(tmp_path) -> None:
    for index in range(1000):
        (tmp_path / f"{index}.txt").write_text("x" * 8192, encoding="utf-8")
    service = FileSearchService(cancellation_grace=1.0)
    tool = GrepTool(service)
    execution = asyncio.create_task(
        tool.execute(json.dumps({"pattern": "not-present", "path": str(tmp_path)}))
    )
    await asyncio.sleep(0.01)
    execution.cancel()

    result = await execution

    assert result.is_error is True
    assert service.active_workers == 0
