"""Coordinator Mode 的双锁、工具白名单与系统提示词。"""

import os

COORDINATOR_ALLOWED_TOOLS = [
    "Agent",
    "TeamCreate",
    "TeamDelete",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
    "read_file",
    "glob",
    "grep",
    "bash",
]

SYSTEM_PROMPT_SUFFIX = """你处于 Coordinator Mode，只负责协调与收敛，不直接修改文件。

按 Research、Synthesis、Implementation、Verification 四阶段推进：
先定位并拆解目标，派队员并建立共享任务；综合队员报告后决定实现分工；
最后用 git diff、git status、测试和 git merge 收敛。

派出 Agent 或 SendMessage 后必须停手等待汇报：
禁止立刻调用 read_file、glob、grep、bash 自己重复探索，禁止用 sleep 或 TaskList 轮询凑时间。
本轮只需简短说明已派出的队员和目标，然后结束。
仅允许在 Research 首次定位、Synthesis 读取队员产出的报告、
Verification 执行收敛检查时自行使用读类工具或 bash。
"""


def env_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def is_enabled(config) -> bool:
    return bool(config.features.coordinator_mode) and env_truthy(
        os.environ.get("MEWCODE_COORDINATOR_MODE", "")
        or os.environ.get("NOVACODE_COORDINATOR_MODE", "")
    )


def allowed_tools() -> list[str]:
    return list(COORDINATOR_ALLOWED_TOOLS)


def system_prompt_suffix() -> str:
    return SYSTEM_PROMPT_SUFFIX


__all__ = [
    "COORDINATOR_ALLOWED_TOOLS",
    "allowed_tools",
    "env_truthy",
    "is_enabled",
    "system_prompt_suffix",
]
