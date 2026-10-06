# teaching-runtime-orchestration

## Purpose
统一 TUI 直接驱动 Agent 的运行链，明确上下文、工具、Session 和 Provider 职责。

## Requirements

### Requirement: 唯一真实运行链
系统 SHALL 只保留由 TUI 直接驱动 `Agent` 的真实交互链，并 SHALL 删除没有真实调用方的 `SessionController`、`TurnEngine`、`runtime/ports`、legacy adapter 和旧版 `Task` 兼容入口。

#### Scenario: 从 TUI 发起 Turn
- **WHEN** 用户在 TUI 提交一次输入
- **THEN** TUI 直接启动主 Agent Run，且运行路径不经过已删除的 runtime seam

#### Scenario: 防止兼容结构重新引入
- **WHEN** 架构测试扫描生产源码
- **THEN** 已删除的 runtime 和旧版 `Task` 符号不存在且不能被生产模块导入

### Requirement: Agent 只协调 ReAct 与事件
系统 SHALL 由 `ContextManager` 完成完整上下文准备事务，由 `ToolRunner` 完成完整工具执行事务，并由 `SessionService` 完成完整 Session 生命周期事务；`Agent` 和 `NovaCodeApp` SHALL NOT 复制这些模块的内部步骤。

#### Scenario: 执行包含工具调用的 Agent Run
- **WHEN** Provider 返回一组工具调用
- **THEN** Agent 把整组调用交给 ToolRunner 并消费其有序结果，而不自行执行权限、批准、Hook 或批处理步骤

#### Scenario: 切换 Session
- **WHEN** 用户请求创建、恢复或切换 Session
- **THEN** NovaCodeApp 通过 SessionService 完成事务并只处理最终 UI 状态

### Requirement: Compact Hook 对应真实历史摘要
系统 SHALL 仅在执行 Layer 2 历史摘要时依次派发 `PRE_COMPACT` 和 `POST_COMPACT`，并 SHALL NOT 因 Layer 1 工具结果卸载或无变化的压缩检查派发 Compact Hook。

#### Scenario: 仅卸载大体积工具结果
- **WHEN** 上下文准备只执行 Layer 1 工具结果卸载
- **THEN** 上下文被更新但 PRE_COMPACT 和 POST_COMPACT 均不触发

#### Scenario: 执行历史摘要
- **WHEN** 自动、手动或紧急策略实际执行 Layer 2 历史摘要
- **THEN** PRE_COMPACT 在摘要前触发，POST_COMPACT 在摘要和 token 锚点更新后触发

### Requirement: Token 锚点不重复计数
系统 SHALL 从压缩后实际保留的消息建立 token 锚点，并 SHALL 对后续新增消息只累计一次。

#### Scenario: 压缩后追加 assistant 输出
- **WHEN** 系统完成历史摘要并在同一 Agent Run 中追加新的 assistant 消息
- **THEN** 后续上下文 token 估算只包含该 assistant 消息一次

### Requirement: Session 恢复原子提交
系统 SHALL 在提交当前 Session 状态前完成目标 Session 的读取、校验、恢复压缩和 Writer 准备；任一步失败 SHALL 保留原 Session 及其可用资源。

#### Scenario: 恢复过程失败
- **WHEN** 目标 Session 的读取、压缩或 Writer 初始化发生错误
- **THEN** 当前 Session、消息、Writer 和 Hook 状态保持恢复前值，临时资源被关闭并显示错误

#### Scenario: 恢复过程成功
- **WHEN** 目标 Session 的所有准备步骤完成
- **THEN** SessionService 一次性切换当前状态且后续事件只写入目标 Session

### Requirement: Provider 由应用单一拥有
系统 SHALL 让主 Agent、普通 SubAgent、SubAgent Hook、进程内 Team 成员和 Memory Governance 借用当前 Provider 实例，并 SHALL 仅由应用所有者在切换或退出时异步关闭一次。

#### Scenario: 多个运行角色共享 Provider
- **WHEN** 一个 Session 先后或并发启动主 Agent、SubAgent Hook 和 Memory Governance
- **THEN** 它们使用同一 Provider 实例且任何借用者都不关闭它

#### Scenario: 应用退出
- **WHEN** 所有借用者停止且应用完成资源关闭
- **THEN** Provider 的异步 close 恰好被调用一次
