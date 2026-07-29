from novacode.tool.filter import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
    FilterParams,
    apply_agent_tool_filter,
)


def test_constants_and_filter_layers() -> None:
    assert ALL_AGENT_DISALLOWED_TOOLS == ["Agent"]
    assert CUSTOM_AGENT_DISALLOWED_TOOLS == []
    assert "read_file" in ASYNC_AGENT_ALLOWED_TOOLS
    result = apply_agent_tool_filter(
        FilterParams(
            all=["Agent", "read_file", "write_file", "bash", "mcp__docs", "TaskList"],
            source=2,
            background=True,
            allowed=["read_file", "bash", "mcp__docs"],
            disallowed=["bash"],
        )
    )
    assert result == ["read_file", "mcp__docs"]


def test_fork_keeps_agent_for_nested_guard() -> None:
    result = apply_agent_tool_filter(
        FilterParams(all=["Agent", "read_file"], source=0, background=True, fork=True)
    )
    assert result == ["Agent", "read_file"]
