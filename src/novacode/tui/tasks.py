"""后台任务通知文本。"""

from novacode.task import BackgroundTask


def build_task_notification(task: BackgroundTask) -> str:
    detail = f"Error: {task.err}" if task.err is not None else f"Result: {task.result}"
    return (
        "<task-notification>\n"
        f'Task {task.id} (name="{task.name}"): {task.status.value}\n'
        f"{detail}\n"
        "</task-notification>"
    )


def build_team_update_reminder(messages) -> str:
    lines = ["<team-update>", f"收到 {len(messages)} 条队员消息:"]
    for index, message in enumerate(messages, 1):
        content = message.content[:8000]
        lines.append(
            f"[{index}] team={message.team_name} from={message.from_} "
            f"type={message.type} ts={message.time}:\n{content}"
        )
    lines.append("</team-update>")
    return "\n".join(lines)
