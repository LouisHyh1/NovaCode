from novacode.agent.fork import build_forked_messages, is_fork_context
from novacode.llm import Message, ToolCall


def test_build_forked_messages_clones_and_repairs_pending_tools() -> None:
    parent = [
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="one", name="read_file", input="{}")],
        ),
    ]
    forked = build_forked_messages(parent, "inspect")
    assert forked is not parent
    assert forked[-2].role == "tool"
    assert forked[-2].tool_results[0].tool_call_id == "one"
    assert forked[-2].tool_results[0].is_error
    assert forked[-1].content.startswith("<fork_boilerplate>")
    assert forked[-1].content.endswith("inspect")
    assert is_fork_context(forked)
    assert not is_fork_context(parent)
