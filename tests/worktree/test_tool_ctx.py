import json
from pathlib import Path

import pytest

from novacode.tool import cwd_from_ctx, resolve_path, with_cwd
from novacode.tool.bash import BashTool
from novacode.tool.edit_file import EditFileTool
from novacode.tool.glob_tool import GlobTool
from novacode.tool.grep_tool import GrepTool
from novacode.tool.read_file import ReadFileTool
from novacode.tool.write_file import WriteFileTool


def test_ctx_helpers(tmp_path: Path) -> None:
    assert cwd_from_ctx() is None
    assert Path(resolve_path("a.txt")) == Path.cwd() / "a.txt"
    absolute = str(tmp_path / "absolute.txt")
    assert resolve_path(absolute) == absolute
    with with_cwd(str(tmp_path)):
        assert cwd_from_ctx() == str(tmp_path)
        assert resolve_path("") == str(tmp_path)
        assert Path(resolve_path("a.txt")) == tmp_path / "a.txt"
    assert cwd_from_ctx() is None


def test_ctx_remaps_parent_absolute_path_into_worktree(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    worktree = parent / ".novacode" / "worktrees" / "agent-test"
    outside = tmp_path / "outside.txt"
    with with_cwd(str(worktree), parent_cwd=str(parent)):
        assert resolve_path(str(parent / "witness.txt")) == str(worktree / "witness.txt")
        assert resolve_path(str(worktree / "witness.txt")) == str(worktree / "witness.txt")
        assert resolve_path(str(outside)) == str(outside)


@pytest.mark.asyncio
async def test_file_tools_use_ctx_cwd(tmp_path: Path) -> None:
    with with_cwd(str(tmp_path)):
        write = await WriteFileTool().execute(json.dumps({"path": "a.txt", "content": "old"}))
        assert not write.is_error
        read = await ReadFileTool().execute(json.dumps({"path": "a.txt"}))
        assert "old" in read.content
        edit = await EditFileTool().execute(
            json.dumps({"path": "a.txt", "old_string": "old", "new_string": "new"})
        )
        assert not edit.is_error
        assert (tmp_path / "a.txt").read_text() == "new"


@pytest.mark.asyncio
async def test_search_and_bash_tools_use_ctx_cwd(tmp_path: Path) -> None:
    (tmp_path / "probe.txt").write_text("needle\n", encoding="utf-8")
    with with_cwd(str(tmp_path)):
        glob = await GlobTool().execute(json.dumps({"pattern": "*.txt"}))
        grep = await GrepTool().execute(json.dumps({"pattern": "needle"}))
        bash = await BashTool().execute(json.dumps({"command": "pwd"}))
    assert glob.content == "probe.txt"
    assert "probe.txt:1:needle" in grep.content
    assert str(tmp_path) in bash.content


def test_tool_schemas_do_not_expose_cwd() -> None:
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        BashTool(),
        GlobTool(),
        GrepTool(),
    ):
        assert "cwd" not in tool.parameters()["properties"]
