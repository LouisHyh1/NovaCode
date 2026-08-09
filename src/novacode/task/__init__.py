"""Agent Run 兼容门面；旧 Task 命名保留一个发布周期。"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from novacode.task.manager import (
    AgentRun,
    AgentRunManager,
    AgentRunPartialState,
    AgentRunStatus,
    AgentRunUsage,
    TaskBusy,
    TaskNotFound,
)
from novacode.task.tools import SendMessageTool, TaskGetTool, TaskListTool, TaskStopTool

if TYPE_CHECKING:
    BackgroundTask = AgentRun
    Manager = AgentRunManager
    PartialState = AgentRunPartialState
    Status = AgentRunStatus
    Usage = AgentRunUsage

_LEGACY_EXPORTS = {
    "BackgroundTask": AgentRun,
    "Manager": AgentRunManager,
    "PartialState": AgentRunPartialState,
    "Status": AgentRunStatus,
    "Usage": AgentRunUsage,
}


def __getattr__(name: str) -> object:
    value = _LEGACY_EXPORTS.get(name)
    if value is None:
        raise AttributeError(name)
    warnings.warn(
        f"novacode.task.{name} 已弃用；请改用对应的 AgentRun 命名",
        DeprecationWarning,
        stacklevel=2,
    )
    return value


__all__ = [
    "AgentRun",
    "AgentRunManager",
    "AgentRunPartialState",
    "AgentRunStatus",
    "AgentRunUsage",
    "BackgroundTask",
    "Manager",
    "PartialState",
    "SendMessageTool",
    "Status",
    "TaskBusy",
    "TaskGetTool",
    "TaskListTool",
    "TaskNotFound",
    "TaskStopTool",
    "Usage",
]
