"""Fork 子 Agent 的消息构造与标记识别。"""

import copy

from novacode.llm import ROLE_ASSISTANT, ROLE_TOOL, Message, ToolResult

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"
FORK_BOILERPLATE = """<fork_boilerplate>
你是一个 Fork 出来的工作进程，不是主 Agent。
规则：不能再 Fork；不要提问或请求确认；直接使用工具；严格限制在分配范围内；
最终报告以 "Scope:" 开头，500 字以内。
</fork_boilerplate>

"""


def build_forked_messages(parent_msgs: list[Message], task: str) -> list[Message]:
    messages = copy.deepcopy(parent_msgs)
    consumed = {
        result.tool_call_id
        for message in messages
        if message.role == ROLE_TOOL
        for result in message.tool_results
    }
    pending = []
    if messages and messages[-1].role == ROLE_ASSISTANT:
        pending = [call for call in messages[-1].tool_calls if call.id not in consumed]
    if pending:
        messages.append(
            Message(
                role=ROLE_TOOL,
                tool_results=[
                    ToolResult(
                        tool_call_id=call.id,
                        content="[forked, skipped]",
                        is_error=True,
                    )
                    for call in pending
                ],
            )
        )
    messages.append(Message(role="user", content=FORK_BOILERPLATE + task))
    return messages


def is_fork_context(messages: list[Message]) -> bool:
    return any(FORK_BOILERPLATE_TAG in message.content for message in messages)
