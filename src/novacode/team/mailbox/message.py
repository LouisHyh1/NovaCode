"""Team 邮箱消息模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class MessageType(StrEnum):
    TEXT = "text"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_REQUEST = "plan_approval_request"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"


@dataclass
class Message:
    from_: str
    text: str
    timestamp: str = ""
    read: bool = False
    type: MessageType = MessageType.TEXT
    request_id: str = ""
    approve: bool | None = None

    def to_dict(self) -> dict:
        value = {
            "from": self.from_,
            "text": self.text,
            "timestamp": self.timestamp or datetime.now(UTC).isoformat(),
            "read": self.read,
        }
        if self.type is not MessageType.TEXT:
            value["type"] = self.type.value
            value["requestId"] = self.request_id
            value["approve"] = self.approve
        return value

    @classmethod
    def from_dict(cls, value: dict) -> Message:
        return cls(
            from_=str(value.get("from", "")),
            text=str(value.get("text", "")),
            timestamp=str(value.get("timestamp", "")),
            read=bool(value.get("read", False)),
            type=MessageType(value.get("type", MessageType.TEXT)),
            request_id=str(value.get("requestId", "")),
            approve=value.get("approve"),
        )
