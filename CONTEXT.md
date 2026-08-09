# NovaCode

NovaCode 是终端 AI 编程助手领域，负责把用户意图转化为受控的 Agent 执行与团队协作。

## Agent 执行

**Agent Run**:
Agent 针对一次请求进行的一次执行，具有明确的开始、结束、结果与运行状态。
_Avoid_: Task、Background Task

**Agent ID**:
在 NovaCode 中唯一标识一个 Agent 的身份。
_Avoid_: Member Name、无作用域的名称

## Team 协作

**Team ID**:
在 NovaCode 中唯一标识一个 Team 的身份。
_Avoid_: 显示名称、目录名称

**Member Name**:
用户和 Agent 用于称呼 Team 成员的名称，仅在所属 Team 内唯一。
_Avoid_: 全局 Agent 名称

**Agent Address**:
由 Team ID 与 Member Name 共同组成的 Team 成员地址。
_Avoid_: 无 Team 作用域的 Member Name

**Team Task**:
Team 内可分派、可追踪并可声明依赖关系的协作工作单元。
_Avoid_: Work Item、无上下文的 Task
