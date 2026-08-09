## Why

NovaCode 的功能已覆盖 Agent Run、Team 协作、Worktree、会话与 TUI，但当前存在会破坏正确性和可维护性的边界缺陷：Team 状态可能在跨进程更新时丢失，Team Task 依赖可能部分提交，成员名称可能跨 Team 串线，文件搜索可能无界阻塞事件循环。现有测试全部通过仍无法约束这些失效模式，也无法阻止 `Agent` 与 TUI 继续吸收职责。

## What Changes

- 为 Team、Team Task Graph 与 Mailbox 建立独立的异步事务型 Repository 边界，保留 JSON 存储，同时增加跨进程所有权锁、唯一临时文件、原子提交、schema 迁移和损坏状态失败关闭。
- 将成员寻址改为 Team 作用域地址：`Agent ID` 全局唯一，`Member Name` 仅在所属 Team 内唯一；`Agent Run` 使用独立注册表。
- 将 Team Task 依赖定义为严格 DAG；创建与更新必须在单次事务中校验引用、自依赖和循环依赖，失败不得留下部分写入。
- 新增有界、非阻塞的 `SearchService`，统一支撑 `glob`、`grep` 和受限文件读取；达到预算时返回部分结果与结构化截断信息。
- 引入类型化领域错误与多资源操作报告，替代无法审计的宽泛异常吞没。
- 定义 `TurnEngine` 的异步类型化事件流接口和显式会话对象 `SessionController`，并用依赖边界测试固定 Ports and Adapters 方向；本次不迁移完整 Agent/TUI 行为。
- 保留旧 `novacode.task`、`TaskList` 和 `TaskGet` 作为兼容门面，在至少一个版本周期内转发到新语义。
- 渐进增加 format、启动 smoke、目标模块 mypy strict、架构依赖、跨进程并发与故障注入门禁，并保留全量测试、双平台 CI 和 tmux 真实交互验收。

## Capabilities

### New Capabilities

- `team-collaboration-state`: Team 作用域身份、事务型 JSON Repository、Team Task DAG、版本化迁移、损坏保护和清理报告。
- `bounded-file-search`: 有界非阻塞搜索、统一忽略策略、资源预算、部分结果和结构化截断诊断。
- `runtime-boundaries`: `TurnEngine`、`SessionController`、兼容门面、类型化错误以及 Ports and Adapters 依赖约束。
- `incremental-quality-gates`: 渐进式静态检查、架构测试、并发与故障测试、双平台 CI 和 tmux 验收要求。

### Modified Capabilities

无。当前 OpenSpec 尚无已建立的主规格。

## Impact

- 主要影响 `src/novacode/team/`、`src/novacode/task/`、`src/novacode/tool/`、`src/novacode/agent/`、`src/novacode/tui/`、CLI 组装与相应测试。
- Team 持久化文件将增加 schema 版本和稳定 Team ID；旧格式通过原子迁移兼容，不要求用户删除现有状态。
- 文件搜索结果协议将增加截断元数据；旧工具名与基本文本结果保持兼容。
- 开发依赖新增 mypy；CI 增加格式、启动、类型和架构检查。
- 本次不完整迁移 `Agent.run` 或 `NovaCodeApp`，只建立后续迁移所需且可执行验证的接口边界。
