## REMOVED Requirements

### Requirement: Turn Engine exposes a typed asynchronous event stream
**Reason**: 后续变更将真实运行链统一为 TUI 直接驱动 Agent，并删除无真实调用方的 TurnEngine、runtime/ports 和 legacy adapter；当前行为由 teaching-runtime-orchestration 规格约束。
**Migration**: 调用方直接消费 Agent 事件，不再通过 Turn Engine 接缝。

### Requirement: Session Controller owns one explicit session lifecycle
**Reason**: Session 生命周期与资源状态已由 SessionService 单一管理，SessionController 已删除。
**Migration**: 创建、恢复、切换、记录和关闭 Session 均通过 SessionService 完成。

### Requirement: Compatibility facades remain during migration
**Reason**: 较早变更引入的兼容期已结束，后续变更明确删除旧版 Task 别名和兼容门面；现有 TaskList/TaskGet 工具保留其 Agent Run 与 Team Task 语义。
**Migration**: 旧导入迁移到 AgentRun、AgentRunManager 或 Team Task Repository，避免重建兼容状态。

### Requirement: This change creates seams without full UI migration
**Reason**: 后续变更已完成真实调用链迁移，并删除原先为渐进迁移保留的无调用方接缝；该阶段性约束已不适用于当前主规格。
**Migration**: 按 teaching-runtime-orchestration 的模块职责维护 Agent、ContextManager、ToolRunner 和 SessionService。
