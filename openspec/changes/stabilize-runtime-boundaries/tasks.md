## 1. 固定基线与变更边界

- [x] 1.1 记录当前锁文件校验、全量 pytest、Ruff lint、非交互启动和现有 Team/Search 行为基线，确保后续失败可归因于本变更
- [x] 1.2 建立仅覆盖本变更新增模块的 Ruff format、严格 mypy 和复杂度/宽泛异常增量检查入口
- [x] 1.3 添加架构测试骨架，声明 runtime、application、team、adapters 与 CLI 组合根的允许依赖方向
- [x] 1.4 为 Team、Team Task Graph、Mailbox 和搜索测试建立隔离的临时状态目录及跨平台 fixture

## 2. 领域类型、错误与端口

- [x] 2.1 新增 `TeamId`、`AgentId`、`MemberName`、`AgentAddress`、`AgentRun` 与 `TeamTask` 类型，并测试 Team 内名称唯一和跨 Team 同名语义
- [x] 2.2 新增 conflict、validation、dependency cycle、corruption、migration、cancellation、timeout 与 cleanup 类型化错误
- [x] 2.3 新增 `OperationReport` 及资源级结果类型，测试总体状态、错误类别和残留路径的聚合规则
- [x] 2.4 定义异步 `TeamRepository`、`TeamTaskRepository`、`MailboxRepository` 与 `SearchService` Protocol，不在端口中导入具体文件系统或 UI 类型
- [x] 2.5 扩展架构测试，验证新领域与端口模块不能导入 Textual、具体 Provider SDK、文件仓储、tmux 或 CLI

## 3. 事务型 JSON 基础设施

- [x] 3.1 先添加真实多进程竞争测试，覆盖活锁不可被抢占、所有权不匹配不可释放和两个成功写入不丢失
- [x] 3.2 实现带 PID、创建时间和随机 token 的跨进程锁，并封装 Windows/Linux 的进程存活与死锁回收策略
- [x] 3.3 实现锁内重读、完整候选校验、唯一 transaction 临时文件、flush/fsync、`os.replace` 和目录 fsync 的原子写入流程
- [x] 3.4 将事务型 JSON 适配器的阻塞 I/O 放入工作线程，并测试调用期间事件循环仍可调度
- [x] 3.5 添加故障注入点和测试，覆盖加锁、候选写入、原子发布前后与清理失败，证明旧权威状态不会被半成品替换

## 4. 聚合仓储、版本迁移与损坏保护

- [x] 4.1 实现 versioned `TeamRepository`，让 Team 配置、成员与活动状态在单个聚合事务内更新
- [x] 4.2 实现 versioned `TeamTaskRepository`，把完整任务图作为一个候选状态读取、校验和发布
- [x] 4.3 实现按 `AgentId` 寻址的 `MailboxRepository`，并用多进程测试证明成功追加的消息不丢失且不重复
- [x] 4.4 为旧 Team 与 Team Task JSON 添加“完整校验→不可覆盖备份→原子前向迁移”，并验证稳定 `TeamId` 和兼容字段
- [x] 4.5 为不可解析、结构无效和未知 schema 版本添加 recovery-required 状态，测试诊断可读、正文不泄漏且后续写入关闭
- [x] 4.6 添加回滚 fixture，证明迁移备份可恢复且失败迁移不会删除原文件、备份或恢复证据

## 5. Team 作用域身份与消息路由

- [x] 5.1 用 Team 成员目录替代全局成员名称注册，保留独立的 `AgentRunRegistry`
- [x] 5.2 将 Team 创建、加载和成员增删路径切换到 `TeamId` 与 `AgentAddress`，拒绝同一 Team 内的重复名称且不产生部分状态
- [x] 5.3 将进程内与持久化消息路由改为“先 Team、再 Member Name 或 Agent ID”解析
- [x] 5.4 添加两个 Team 都包含 `alice` 的集成测试，证明 send/message/mailbox 只到达目标 Team 的成员
- [x] 5.5 移除 Team 重载路径中的静默损坏覆盖和仅进程内锁依赖，所有写入统一委托给 Repository

## 6. Team Task DAG 与成员移除

- [x] 6.1 先添加缺失 blocker、自依赖、直接环、间接环与有效依赖的失败/成功测试，并检查失败前后权威文件字节不变
- [x] 6.2 实现完整候选图的节点存在性、唯一性、无自依赖、无环和双向边一致性校验
- [x] 6.3 将 Team Task 创建与依赖更新改为单事务提交，读取时从已提交图派生 readiness
- [x] 6.4 实现普通成员移除的前置检查，存在未完成归属任务时拒绝并返回所有阻塞 Team Task ID
- [x] 6.5 实现强制移除 Saga：先解绑未完成任务并固化已完成任务的历史负责人，再删除成员，最后清理外部资源
- [x] 6.6 为 Saga 每一步生成 `OperationReport`，测试成员删除或资源清理失败时留下的是可重试安全状态而非悬空引用
- [x] 6.7 将 Team 删除、成员移除和清理调用点从宽泛异常吞没改为类型化错误与完整报告

## 7. 有界 Search Service

- [x] 7.1 为结果数、扫描文件数、单文件字节、总字节和单调时限建立预算类型及默认值与安全上限测试
- [x] 7.2 实现共享目录遍历和 ignore policy，默认排除 Git、虚拟环境、`node_modules`、缓存与 NovaCode 管理的 Worktree
- [x] 7.3 实现工作线程中的 glob/grep 扫描，在目录项与固定大小文件块之间检查 deadline 和线程安全 stop event
- [x] 7.4 实现取消收尾上限，并用并发 tick 与取消测试证明事件循环响应和后台扫描不会无界继续
- [x] 7.5 实现 `SearchResult` 的 hits、truncated、reason、scanned_files、scanned_bytes 与 elapsed 元数据，保留超时前的部分命中
- [x] 7.6 实现读取期间生效的有界文件读取，默认最多返回 2,000 行和 256 KiB 可见文本且不按完整文件大小分配内存
- [x] 7.7 添加敏感文件与路径测试，证明自定义 ignore/预算、诊断和截断元数据都不能绕过或泄漏既有保护

## 8. 搜索与文件工具适配

- [x] 8.1 将现有 `glob` 工具改为调用 Search Service，保持人类可读正文并在 `Result.metadata` 暴露结构化截断信息
- [x] 8.2 将现有 `grep` 工具改为调用 Search Service，保持既有匹配展示、权限检查与错误契约
- [x] 8.3 将 `read_file` 改为调用有界读取适配器，删除“完整读入后截断”的执行路径
- [x] 8.4 添加大目录、无匹配、命中上限、超时、超大文件和取消的工具级回归测试

## 9. Turn Engine 与 Session Controller 接缝

- [x] 9.1 定义 `TurnRequest`、text/tool/approval/usage/done/cancelled/error `TurnEvent` 联合与 `TurnEngine.run()` 异步迭代协议
- [x] 9.2 定义 Provider、ToolExecutor、PermissionPolicy、HookDispatcher 与 ConversationStore Port，并用架构测试固定向内依赖
- [x] 9.3 实现无第二份运行状态的 `LegacyAgentTurnEngine`，把现有 `Agent.run` 事件映射到新类型化事件流
- [x] 9.4 测试正常完成、工具调用、审批、错误和取消映射，证明取消流不能同时报告成功完成
- [x] 9.5 实现接收不可变 `SessionDependencies` 的 `SessionController` 及 `start/submit/switch_session/cancel/close` 状态机
- [x] 9.6 测试 session switch 的候选先准备和原子替换语义，以及失败时旧会话继续活动
- [x] 9.7 测试 `close` 幂等、资源最多释放一次，并通过 `OperationReport` 暴露全部清理结果
- [x] 9.8 在 CLI 组合根接通 Legacy Turn Engine 与 Session Controller 最小实例化路径，不迁移完整 `Agent.run` 或 `NovaCodeApp`

## 10. 兼容门面与调用点迁移

- [x] 10.1 保留 `novacode.task` 导入路径，将旧执行类型转发到 Agent Run 语义且不维护第二份状态
- [x] 10.2 保留 `TaskList`/`TaskGet` 工具名称：无 Team 上下文返回 Agent Run，Team 上下文委托 Team Task Repository
- [x] 10.3 添加旧导入、旧工具名和 Team 上下文分流回归测试，并为兼容门面发出可测试的弃用信号
- [x] 10.4 将 Team Manager、协作工具与现有 TUI 调用点切换到新端口，同时验证用户可见命令和输出保持兼容

## 11. 自动化质量门禁

- [x] 11.1 将 Ruff lint、Ruff format check、严格目标模块 mypy、架构测试和非交互启动/version smoke 加入本地验证命令
- [x] 11.2 更新 CI，使 `ubuntu-latest` 与 `windows-latest` 运行锁文件校验、全量 pytest、Ruff、format、目标 mypy、架构测试和 smoke
- [x] 11.3 运行事务仓储多进程与故障注入套件，确认在两个支持平台上无丢失更新、活锁误回收或半提交
- [x] 11.4 运行全量回归并修复本变更引入的失败，不把存量 213 项复杂度/宽泛异常告警扩展为无关清理

## 12. 真实交互验收

- [x] 12.1 在 tmux 启动 NovaCode，执行正常读文件并回答的真实对话，确认工具调用和最终回复未回归
- [x] 12.2 在 tmux 执行两个 Team 同名成员的消息路由与任务依赖场景，确认作用域身份和失败原子性
- [x] 12.3 在 tmux 使用刻意调小的预算搜索大型 fixture，确认界面保持响应、返回部分结果并解释截断原因
- [x] 12.4 汇总自动化与 tmux 的任务专属验收结果、残留风险和后续完整 Agent/TUI 迁移边界
