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
