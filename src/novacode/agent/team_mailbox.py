"""在 Agent Loop 调用 LLM 前注入 Team 未读消息。"""

from novacode.permission import Mode


async def ingest_team_mailbox(agent) -> None:
    context = getattr(agent, "teammate_context", None)
    if context is None:
        return
    indices, messages = await context.mailbox.read_unread(context.agent_id)
    if not messages:
        return
    lines = ["<incoming-messages>", f"收到 {len(messages)} 条新消息:"]
    for index, message in enumerate(messages, 1):
        lines.extend(
            (
                f"[{index}] 来自 {message.from_}"
                f"(type={message.type.value},ts={message.timestamp}):",
                f"    {message.text}",
            )
        )
        if message.type.value == "plan_approval_response":
            if message.approve is True:
                agent.permission_mode = Mode.DEFAULT
                lines.append("Lead 已批准计划，权限模式已切换到 default，可执行计划。")
            elif message.approve is False:
                agent.permission_mode = Mode.PLAN
                lines.append(f"Lead 驳回了计划，反馈：{message.text}。请调整后重新提交。")
    lines.append("</incoming-messages>")
    agent.runtime.append_reminders(["\n".join(lines)])
    await context.mailbox.mark_read(context.agent_id, indices)
