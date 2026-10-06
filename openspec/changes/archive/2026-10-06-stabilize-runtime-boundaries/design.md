## Context

NovaCode 当前由一个功能完整但边界逐渐模糊的运行时组成。`Agent.run` 同时协调上下文压缩、Provider 流、Hook、权限、审批、工具并发、记忆与持久化；`NovaCodeApp` 同时承担 Textual 渲染、Provider 选择、会话切换、Skill、Worktree、Team、资源关闭和 Agent 生命周期。Team 协作又把长期 Team Task 与临时 Agent Run 复用在同一组名称和注册表中。

这次变更优先处理已经能导致错误状态或事件循环阻塞的问题，并建立后续拆分所需的可执行接缝。现有 JSON 文件的可读性、Windows/Linux 支持、工具名称和用户交互需要保持兼容。`docs/` 不属于本变更的输入或输出；决策来源是代码证据、根级 `CONTEXT.md` 与本 OpenSpec change。

## Goals / Non-Goals

**Goals:**

- 消除 Team 跨进程丢失更新、名称串线、损坏覆盖和 Team Task 半提交。
- 把 Team、Team Task Graph、Mailbox 建模为三个可验证的事务聚合。
- 让搜索和大文件读取具有明确资源预算、取消语义和结构化截断结果。
- 引入 Agent Run、Team Task、Agent Address 等稳定语言，并保留旧 API 兼容门面。
- 定义 `TurnEngine` 和 `SessionController` 的最小可执行合同，使用架构测试固定依赖方向。
- 用类型化错误、操作报告和渐进质量门禁阻止相同债务继续增长。

**Non-Goals:**

- 本次不把完整 `Agent.run` 实现迁入新 Turn Engine。
- 本次不把完整 `NovaCodeApp` 生命周期迁入 Session Controller。
- 本次不迁移到 SQLite，不引入 Web/GUI 客户端或通用事件总线。
- 本次不删除 `novacode.task`、`TaskList`、`TaskGet` 等兼容接口。
- 本次不清理全仓全部复杂度或宽异常告警。

## Decisions

### 1. 采用清晰领域语言并保留兼容门面

临时执行统一称为 `AgentRun`，长期协作工作统一称为 `TeamTask`。`Task` 不再进入新的无上下文领域 API。现有 `novacode.task` 以及 `TaskList`/`TaskGet` 工具保留至少一个发布周期，但实现只转发到新服务，不能维护第二份状态。

相比一次性破坏性重命名，这允许现有测试、Skill、历史调用和 TUI 渐进迁移；相比只改注释，它能让类型和模块边界实际反映语义。

### 2. Team 身份采用作用域地址

新增稳定 `TeamId`，继续使用全局唯一 `AgentId`；`MemberName` 只在 Team 内唯一，`AgentAddress = (TeamId, MemberName)`。Team 成员目录按地址解析，Agent Run 使用独立 `AgentRunRegistry`。消息工具先确定 Team，再在该 Team 内按 Member Name 或 Agent ID 解析。

这避免全局名称表静默覆盖，也不要求用户为不同 Team 人为设计全局唯一名称。旧 JSON 的显示名称和 sanitized name 继续保留，但都不承担稳定身份。

### 3. 保留 JSON，但所有状态经异步事务型 Repository

建立三个接口：

- `TeamRepository`：Team 配置、成员和活动状态。
- `TeamTaskRepository`：完整 Team Task DAG。
- `MailboxRepository`：按 Agent ID 寻址的消息流。

接口全部为 async；具体 JSON 适配器用 `asyncio.to_thread()` 执行阻塞文件操作。每个写事务遵循：

1. 获取带 PID、创建时间和随机所有权 token 的跨进程锁。
2. 在锁内重新读取并校验当前权威状态。
3. 在内存候选上应用完整操作并验证不变量。
4. 写入带唯一 transaction ID 的临时文件并 flush/fsync。
5. `os.replace()` 原子发布并 fsync 目录。
6. 仅当 lock token 仍属于当前持有者时释放锁。

活 PID 的锁不可按时间直接抢占；死进程锁只有在身份与平台检查允许时才回收。写入失败不发布候选。相比 SQLite，这保留当前可调试性且避免 schema/数据库迁移成本；相比局部补锁，Repository 能集中强制不变量。

### 4. Team Task Graph 在一个事务内验证为 DAG

Repository 每次对完整候选图执行：

- 所有 task ID 唯一。
- 所有边端点存在。
- 无自依赖。
- DFS 或拓扑排序证明无环。
- `blocked_by` 与 `blocks` 双向一致。
- `is_ready` 只在读取时从已提交图派生。

创建任务及补全反向边不再分成多次写入。任何校验失败都拒绝整个候选。

### 5. 跨聚合成员删除使用安全 Saga

普通删除先查询未完成 Team Task，存在归属时拒绝并报告 ID。强制删除按固定顺序执行：

1. Team Task Graph 事务把未完成任务改为未分派，并把已完成任务的负责人转换为历史快照。
2. Team 事务删除成员。
3. 清理 Session、Worktree 和 Backend 资源。

第一步成功、第二步失败时只会留下“成员仍存在但任务已解绑”的安全中间态，可重试；绝不出现任务指向已删除成员。每一步进入 `OperationReport`，清理失败不会被吞没。

### 6. 版本化迁移失败关闭

Team 和 Team Task JSON 顶层增加 `schema_version`，Team 增加 `team_id`。首次打开旧文件时：

1. 完整解析和校验旧结构。
2. 生成不可覆盖的迁移备份。
3. 生成新结构，同时保留旧代码能够忽略的兼容字段。
4. 使用 Repository 事务原子发布。

解析失败、校验失败或遇到未知新版本时抛 `StateCorruptionError`/`UnsupportedSchemaError`，记录路径但不泄漏正文；该聚合进入 recovery-required，写入被拒绝。回滚代码时可继续读取保留字段，必要时在 NovaCode 停止后恢复备份。

### 7. Search Service 提供共享预算和合作式取消

新增标准库实现的 `SearchService`，供 `glob`、`grep` 和有界文件读取适配器使用。默认预算：

- 最多 100 个结果。
- 最多扫描 20,000 个文件。
- 单文件最多读取 2 MiB。
- 总读取量最多 64 MiB。
- 最长 10 秒。

扫描在工作线程执行，按目录项和文件块检查 monotonic deadline 与线程安全 stop event。async 调用被取消时先设置 stop event，再等待有界收尾。默认忽略 `.git`、`.venv*`、`node_modules`、`__pycache__`、`.pytest_cache`、`.ruff_cache` 和 NovaCode 管理的 Worktree；项目配置只能在安全上限内调整预算，不能关闭敏感文件过滤。

内部返回 `SearchResult(hits, truncated, reason, scanned_files, scanned_bytes, elapsed)`。工具仍输出人类可读文本，同时在向后兼容的 `Result.metadata` 中公开结构化信息。`read_file` 改为分块读取，在达到 2,000 行、256 KiB 可见文本或读取预算时停止，不再先载入完整文件。

强制依赖 `ripgrep` 虽然更快，但会引入目标机器前置条件；只用 `to_thread` 包裹旧实现又无法解决无命中和全量收集问题。

### 8. Turn Engine 使用类型化异步事件合同

在核心边界定义：

- `TurnRequest`：会话视图、用户输入、模式和取消句柄。
- `TurnEvent` 联合：text、tool、approval、usage、done、cancelled、error。
- `TurnEngine` Protocol：`run(request) -> AsyncIterator[TurnEvent]`。
- Provider、ToolExecutor、PermissionPolicy、HookDispatcher、ConversationStore Port。

本次提供 `LegacyAgentTurnEngine`，把现有 `Agent.run` 事件映射到新合同，以证明接口可用但避免行为重写。核心合同不导入 Textual、SDK Provider、文件 Repository、tmux 或 CLI。

纯 reducer/effect 模型的可测试性更高，但需要一次性重写；回调模型会使取消与错误传播继续分散。异步迭代与现有消费方式最兼容。

### 9. Session Controller 是显式会话对象

`SessionController` 接收不可变 `SessionDependencies`，拥有一个活动会话的 Turn Engine、Writer、审批状态和取消状态，并公开 `start/submit/switch_session/cancel/close`。本次实现合同、状态转换测试与旧运行时适配器，不迁移全部 TUI。

会话切换先准备 detached candidate 和新资源，成功后原子替换，再关闭旧资源；失败保持旧会话。`close()` 幂等并返回操作报告。应用级单例会形成新的 God Object，因此不采用。

### 10. 类型化错误与操作报告

新增稳定错误类别：`ConflictError`、`ValidationError`、`DependencyCycleError`、`StateCorruptionError`、`MigrationError`、`OperationCancelled`、`OperationTimeout`、`CleanupError`。Repository 与核心抛类型化错误，CLI/TUI/Tool 适配器负责转换为日志和用户文本。

`OperationReport` 包含 operation、resource、status、error category、message 和 residual path。只有“资源本就不存在”等可证明幂等成功的异常可以转为成功；其他失败必须进入报告。

### 11. Ports and Adapters 依赖方向由测试强制

建议新增边界：

```text
novacode.runtime       -> 领域类型、协议
novacode.application   -> runtime ports、Team/Search ports
novacode.team          -> Team 领域与 Repository ports
novacode.adapters      -> JSON、Provider SDK、Textual、tmux 适配
novacode.cli           -> 唯一组合根
```

为降低本次迁移量，既有包可以暂时作为 adapter，但架构测试必须禁止新 runtime/domain 模块向 Textual、具体 Provider、filesystem、tmux 和 CLI 反向依赖。

### 12. 质量门禁渐进启用

CI 保留全量 pytest 与现有 Ruff，同时增加：

- `ruff format --check`。
- 对新增 runtime/application/repository/search 边界运行 mypy strict。
- 导入边界测试。
- `python -m novacode --version` 或等价非交互启动 smoke。
- Team Repository 的真实多进程竞争与故障注入测试。
- Windows/Linux 相同门禁。

扩展 Ruff 严格规则只对新增或本次触碰的边界执行，禁止新增复杂度和宽异常，但不要求一次清理全部存量告警。最终在 tmux 中验证正常对话、Team 状态路径和小预算截断搜索。

## Risks / Trade-offs

- [JSON 多文件不能提供数据库级跨聚合原子性] → 强制安全 Saga 顺序，使中间态可重试且不产生悬空引用，并通过 OperationReport 暴露未完成步骤。
- [跨平台 PID 与文件锁语义不同] → 封装平台探测，使用所有权 token，真实 Windows/Linux 多进程测试覆盖活锁、死锁回收和错误释放。
- [工作线程取消不是抢占式] → 扫描器按目录项和固定大小文件块检查 stop event，并给取消收尾设置上限。
- [兼容门面延长双语义存在时间] → 门面不持状态，发出可测试的弃用信号，并在后续 change 中决定移除版本。
- [新接口可能成为未使用抽象] → 提供 LegacyAgentTurnEngine、SessionController 状态测试和组合根契约测试，确保接缝可实例化并真实映射现有事件。
- [迁移或损坏保护使部分用户操作暂时不可用] → 保留不可覆盖备份、提供路径和错误类别、允许只读诊断，禁止自动清空数据。
- [新增门禁扩大 CI 时间] → 类型检查仅覆盖新边界；并发/故障测试保持确定性，耗时场景使用可注入时钟和小型夹具。

## Migration Plan

1. 固定当前全量测试、Ruff、锁文件和启动基线，加入不改变行为的架构测试骨架。
2. 增加领域类型、类型化错误、Repository Protocol、Search Service Protocol、Turn Engine 和 Session Controller 合同。
3. 实现 Team/Team Task/Mailbox JSON Repository、锁与 schema 迁移，并先通过故障注入和多进程测试。
4. 把 Team Manager 与协作工具切换到 Repository；引入 Team 作用域目录和独立 Agent Run Registry；保留旧门面。
5. 把 `glob`、`grep`、`read_file` 切换到 Search Service，并验证敏感文件防线与取消行为。
6. 用 LegacyAgentTurnEngine 和旧会话适配器接通新合同，增加依赖边界、mypy strict、format 和 smoke 门禁。
7. 运行全量本地检查、Windows/Linux CI 和 tmux 真实交互验收。

回滚时先停止 NovaCode。新 JSON 保留旧字段，旧版本可继续读取；若迁移本身需要撤销，则从对应不可覆盖备份恢复。代码回滚不得自动删除新状态、备份或 recovery-required 证据。

## Open Questions

没有未决的产品或架构问题。预算数值是首版安全默认值；实现阶段可以依据确定性基准测试下调，但任何调整都必须保持有限预算、结构化截断和跨平台一致性合同。
