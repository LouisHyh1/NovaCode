## ADDED Requirements

### Requirement: Team 仅支持进程内和 tmux 后端
系统 SHALL 只提供进程内与 tmux Team 后端，并 SHALL 删除 iTerm2 后端、自动检测分支和相关枚举值。

#### Scenario: 自动选择 Team 后端
- **WHEN** NovaCode 在支持 tmux 的环境选择跨进程 Team
- **THEN** 系统选择 tmux；否则使用进程内后端，且不存在 iTerm2 选择结果

#### Scenario: 请求已删除的 iTerm2 后端
- **WHEN** 旧配置请求 iTerm2 Team 后端
- **THEN** 配置校验明确报告不支持，而不是静默选择未测试实现

### Requirement: Repository 是跨进程状态唯一事实来源
每个依赖 Team、成员或 Team Task 状态的操作 SHALL 从 Repository 读取最新快照并在该操作内使用，且 SHALL NOT 依赖长生命周期可变 Team 或 lead 缓存。

#### Scenario: tmux 成员更新 Repository
- **WHEN** 外部 tmux 成员在两个 lead 操作之间更新 Team Task 或成员状态
- **THEN** 后一个 lead 操作读取并使用更新后的 Repository 快照

#### Scenario: 写入 Team 状态
- **WHEN** 一个 Team 操作修改成员或 Team Task
- **THEN** 修改通过 Repository 原子更新且后续操作可立即观察

### Requirement: Team 成员继承父 Provider 配置
进程内 Team 成员 SHALL 借用父 Provider 实例；tmux 子进程 SHALL 从持久化启动信息恢复等价的父 Provider 配置，且 Team 派生参数 SHALL NOT 提供成员级 model 覆盖。

#### Scenario: 创建进程内 Team 成员
- **WHEN** lead 创建进程内成员
- **THEN** 成员使用 lead 的 Provider 实例且不创建新的连接池

#### Scenario: 创建 tmux Team 成员
- **WHEN** lead 启动 tmux 子进程成员
- **THEN** 子进程使用持久化的父 Provider 配置而不是配置列表中的任意第一个 Provider
