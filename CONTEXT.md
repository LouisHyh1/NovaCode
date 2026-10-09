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

## 编程评测

**Evaluation Task**:
用于评估 NovaCode 编程行为的独立问题，具有明确初始条件、用户请求和验收合同，可以包含预先定义的多轮请求。
_Avoid_: Team Task、无作用域的 Task

**Evaluation Run**:
在指定评测配置下对一个 Evaluation Task 进行的一次独立尝试，可以包含多个 Agent Run，并具有完整结果与成本记录。
_Avoid_: Agent Run、把重复运行称为新任务

**Evaluation Campaign**:
共享任务版本、配置、预算与分析规则的一组评测运行及其结果。
_Avoid_: Session、一次模型请求

**Task Contract**:
一个 Evaluation Task 的初始条件、用户目标、允许行为和成功条件的完整约定。
_Avoid_: Agent 自报成功、仅退出码

**Task Family**:
来自同一问题或同源缺陷的一组评测任务及其变体，是开发与冻结划分的隔离单位。
_Avoid_: 仅按任务名称判断独立性

**Development Suite**:
允许用于调试 NovaCode 评测流程、配置和任务合同的任务集合。
_Avoid_: 正式冻结题库

**Frozen Suite**:
在正式实验前确定身份和合同，并用于独立评估而不用于调参的任务集合。
_Avoid_: 开发调试集、直接等同官方数据 split

**Corpus Admission**:
确认一个候选评测任务的初始状态、环境和独立验收有效后，将其纳入可执行题库的过程。
_Avoid_: 仅列出任务候选

## 工具可见性

**Tool Exposure**:
模型在一个执行范围内能够看到的工具定义状态，与工具注册及执行授权分别管理。
_Avoid_: Tool Loading、工具执行授权

**Tool Discovery**:
模型按名称或职责找到当前范围内可用工具，并取得其完整定义的行为。
_Avoid_: MCP 连接建立、自动执行业务工具
