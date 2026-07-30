"""Team 消息工具，并兼容既有命名 SubAgent 续派。"""

import json
from datetime import UTC, datetime

from novacode.task import Status as BackgroundStatus
from novacode.team.backend import new_backend
from novacode.team.mailbox import Box, Message, MessageType
from novacode.team.tools.common import current_teammate_context, parse_args, resolve_team
from novacode.team.types import BackendType
from novacode.tool import Result


class SendMessageTool:
    read_only = False

    def __init__(self, manager, background_manager) -> None:
        self.manager = manager
        self.background_manager = background_manager

    def name(self) -> str:
        return "SendMessage"

    def description(self) -> str:
        return "向 Team 成员/全员发送消息；Team 外可用 name/message 续派命名 SubAgent。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string"},
                "to": {"type": "string"},
                "content": {"type": "string"},
                "name": {"type": "string"},
                "message": {"type": "string"},
                "type": {"type": "string", "enum": [item.value for item in MessageType]},
                "request_id": {"type": "string"},
                "approve": {"type": ["boolean", "null"]},
            },
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        data = parse_args(args)
        to = str(data.get("to") or "")
        content = str(data.get("content") or data.get("message") or "")
        if not to:
            name = str(data.get("name") or "")
            if name and not data.get("team_name") and current_teammate_context() is None:
                try:
                    task_id = await self.background_manager.send_message(name, content)
                except Exception as exc:
                    return Result(f"无法续派任务: {exc}", is_error=True)
                return Result(json.dumps({"task_id": task_id, "status": "resumed"}))
            to = name
        if not to or not content:
            return Result("to/content（或 name/message）为必填项", is_error=True)
        try:
            team = resolve_team(self.manager, data)
            sender_context = current_teammate_context()
            sender = sender_context.member_name if sender_context is not None else "lead"
            type_ = MessageType(data.get("type", MessageType.TEXT))
            request_id = str(data.get("request_id") or "")
            approve = data.get("approve")
            if type_ is MessageType.PLAN_APPROVAL_RESPONSE:
                if sender != "lead":
                    raise ValueError("只有 Lead 可以发送 plan_approval_response")
                if not request_id or type(approve) is not bool:
                    raise ValueError("plan_approval_response 需要 request_id 和 approve")
            if type_ is MessageType.SHUTDOWN_RESPONSE and to != "lead":
                raise ValueError("shutdown_response 只能发给 Lead")
            if to == "*":
                targets = [member for member in team.members if member.name != sender]
            else:
                target_id = self.manager.registry.resolve(to) or to
                target = team.member_by_name(to) or team.member_by_agent_id(target_id)
                if target is None:
                    raise ValueError(f"Team 内找不到收件人: {to}")
                targets = [target]
            delivered = []
            message = Message(
                from_=sender,
                text=content,
                type=type_,
                request_id=request_id,
                approve=approve,
            )
            for target in targets:
                await Box(team.mailbox_dir).write(target.agent_id, message)
                backend = new_backend(target.backend_type, task_mgr=self.background_manager)
                await backend.wake(target.pane_id, target.agent_id)
                if target.backend_type is BackendType.IN_PROCESS and target.name != "lead":
                    background = self.background_manager.get(target.agent_id)
                    if background is not None and background.status is not BackgroundStatus.RUNNING:
                        await team.set_member_active(target.name, True)
                        await self.background_manager.send_message(target.name, content)
                delivered.append(target.agent_id)
        except Exception as exc:
            return Result(f"Team 消息发送失败: {exc}", is_error=True)
        return Result(
            json.dumps(
                {"delivered_to": delivered, "timestamp": datetime.now(UTC).isoformat()},
                ensure_ascii=False,
            )
        )
