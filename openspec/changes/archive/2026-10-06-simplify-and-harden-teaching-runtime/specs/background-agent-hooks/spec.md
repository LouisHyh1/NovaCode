## ADDED Requirements

### Requirement: SubAgent Hook 异步且不阻塞 Turn
`SubagentAction` SHALL 启动后台 Agent Run，并 SHALL 在后台任务完成前允许触发它的 Turn 继续执行或结束。

#### Scenario: Hook 启动慢速 SubAgent
- **WHEN** Hook 触发一个仍在等待 Provider 的 SubAgentAction
- **THEN** 触发该 Hook 的主 Agent Run 无需等待后台结果即可继续

### Requirement: SubAgent Hook 强制只读权限
SubAgent Hook SHALL 强制使用 PLAN/只读权限，并 SHALL 禁止写工具、批准请求、Agent 派生和 Team 派生，不受 Hook 配置或父 Agent 权限模式放宽。

#### Scenario: 后台 Hook 请求写文件
- **WHEN** SubAgent Hook 尝试调用会修改 Workspace 的工具
- **THEN** 调用被拒绝且不会向用户发起批准请求

#### Scenario: 后台 Hook 请求派生 Agent
- **WHEN** SubAgent Hook 尝试调用 Agent 或 Team 工具
- **THEN** 调用被拒绝且不创建新的 Agent Run

### Requirement: SubAgent Hook 不递归派发 Hook
SubAgent Hook 的 Agent Run SHALL NOT 派发任何 Hook 事件。

#### Scenario: 后台 Hook 执行普通工具
- **WHEN** SubAgent Hook 执行一个只读工具且该工具通常存在 PRE_TOOL_USE 或 POST_TOOL_USE Hook
- **THEN** 后台执行不触发这些 Hook，也不形成新的 SubagentAction

### Requirement: 后台结果可见且可回注
SubAgent Hook 完成、失败或取消时系统 SHALL 生成带有来源事件和状态的用户可见通知；已完成结果 SHALL 可作为后续主 Agent Run 的 reminder，而 SHALL NOT 修改已经结束的 Turn。

#### Scenario: 后台 Hook 在 Turn 结束后完成
- **WHEN** SubAgent Hook 在触发它的 Turn 已结束后返回结果
- **THEN** Session 显示完成通知并在下一次主 Agent Run 中提供结果 reminder

#### Scenario: 后台 Hook 失败
- **WHEN** SubAgent Hook 抛出异常或被取消
- **THEN** Session 显示失败或取消通知且主 Agent Run 状态不被改写

### Requirement: 派生 Agent 继承父 Provider
普通 SubAgent、SubAgent Hook 和进程内 Team 成员 SHALL 使用父 Agent 的 Provider，且 SubAgent 定义与 Agent Tool 调用 SHALL NOT 提供独立 model 选择参数。

#### Scenario: 创建普通 SubAgent
- **WHEN** Agent Tool 创建普通 SubAgent
- **THEN** 子 Agent 使用与父 Agent 相同的 Provider 实例

#### Scenario: 提交旧 model 参数
- **WHEN** 调用方在已移除的 SubAgent model 参数位置提交值
- **THEN** 参数校验明确拒绝该值，而不是解析后静默忽略
