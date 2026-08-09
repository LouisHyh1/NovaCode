## Context

NovaCode 当前真正的交互路径由 CLI 创建 TUI，再由 TUI 创建并调用 `Agent`。仓库中另有 `SessionController`、`TurnEngine`、`runtime/ports` 和 legacy adapter，但它们只服务于测试或兼容代码，没有承载真实运行流量。与此同时，`Agent` 和 `NovaCodeApp` 分别集中了承担上下文压缩、工具执行、Session 生命周期、UI 状态和持久化等多种职责。

本项目定位为练手和教学项目。Team 跨进程协作、权限控制、Session 恢复、Hook 和长期记忆作为少量工程亮点保留，但方案必须优先使用已有实现和 Python 标准库，删除未使用的抽象，不为假设中的生产需求增加资源池、适配层或配置旋钮。

## Goals / Non-Goals

**Goals:**

- 只保留一条能够从 CLI 追踪到 Agent 的真实运行链，删除没有生产调用方的兼容结构。
- 让 `Agent` 只协调 ReAct 循环与事件，让上下文、工具和 Session 事务各自集中在一个具有小接口的深模块中。
- 收紧 Workspace、安全配置、Provider、Hook、Memory 和 Team 的生命周期与状态语义。
- 修复长流输出、同步写盘、后台网络等待持锁等可观察的响应性问题。
- 建立适合现状的增量质量门禁，并以行为测试和 tmux 对话验证真实链路。

**Non-Goals:**

- 不清零全项目现有的 mypy、复杂度或宽泛异常历史债务。
- 不引入通用 Ports/Adapters、依赖注入容器、Provider 资源池或动态模型路由。
- 不支持项目目录外的系统临时区，不建设“受管临时目录”注册机制。
- 不保留 iTerm2、旧 `Task` 名称或其他未被真实教学路径使用的兼容能力。
- 不增加第三方依赖，也不建立生产级吞吐量、可用性或覆盖率目标。

## Decisions

### 1. 真实运行链保持为 `TUI -> Agent`

删除 `SessionController`、`TurnEngine`、`runtime/ports`、legacy adapter 及只验证这条虚假 seam 的测试。CLI 和 TUI 继续直接创建真实对象，架构测试改为验证允许的依赖方向和禁止重新引入的符号。

备选方案是把 TUI 逐步迁移到既有 controller/ports。该方案会先维护两条路径，再为单一实现增加大量 `object` 类型接口，不符合教学项目的删除优先原则。

### 2. 从大类中提取三个具体深模块

- `ContextManager` 拥有工具结果卸载、历史摘要、token 锚点、溢出恢复和 Compact Hook 顺序。`Agent` 只提交当前消息与压缩模式并接收准备后的上下文及元数据，不了解 Layer 1/Layer 2 的内部步骤。
- `ToolRunner` 拥有工具调用分组、并发批次、权限检查、用户批准、工具 Hook、取消和结果归一化。`Agent` 只提交一次模型返回的工具调用集合并消费有序结果事件。
- `SessionService` 作为具体代码名称保留，但其设计角色是 Session 模块。它拥有创建、恢复、切换、事件持久化和关闭事务；`NovaCodeApp` 只保留 UI 状态、控件更新和渲染。

三个模块的接口只暴露调用者完成一次完整事务所需的方法，不把内部 helper、状态机步骤或测试替身提升为公共接口。只有 Provider、文件存储等已经存在真实多实现或必要测试替身的 seam 才保留窄 Protocol。

备选方案是仅把 helper 移到新文件，或为每个依赖建立 Port。前者无法通过删除测试，后者制造单实现 seam；两者都拒绝。

### 3. Context Compaction 只指历史摘要

Layer 1 继续负责卸载或替换大体积工具结果，但不触发 `PRE_COMPACT` / `POST_COMPACT`。只有确定需要执行 Layer 2 历史摘要后，`ContextManager` 才依次触发 PRE、执行摘要、更新 token 锚点并触发 POST。结果元数据明确记录是否发生摘要，避免每轮“检查”伪装成压缩事件。

token 锚点从压缩后实际保留的消息计算，新增 assistant 输出只计入一次。自动压缩未达到阈值或熔断时不触发 Compact Hook。

### 4. Session 恢复是可回滚事务

`SessionService` 在临时状态中读取并校验目标 Session、准备恢复压缩和 Writer，再一次性提交当前 Session、消息、Writer 与相关 Hook 状态。任一步失败都关闭临时资源并保留原 Session，不允许出现“旧 Session 已结束但新 Session 未恢复”的半切换状态。

Session 事件保持串行顺序和现有 `fsync` 持久性语义，但阻塞文件操作通过 `asyncio.to_thread()` 执行。单个 Session 内只允许一个写入序列，不增加通用异步 Writer 框架。

### 5. 应用拥有唯一 Provider 生命周期

主 Agent、普通 SubAgent、SubAgent Hook、进程内 Team 成员和 Memory Governance 借用当前同一个 Provider 实例。Provider 的窄接口增加异步 `close()`；只有创建它的应用所有者能在项目切换或退出时调用一次。借用者不得关闭或复制连接池。

SubAgent 定义、Agent Tool 参数和 Team 派生链删除 model 选择字段。多模型教学需求出现前，不保留当前未生效的配置表面。

### 6. Workspace 和权限配置采用显式保守语义

文件工具只能访问解析后的项目根目录及其后代；绝对路径、`..`、符号链接或系统临时路径只要解析后越界均被拒绝。NovaCode 自身需要的状态和中间文件放在项目内 `.novacode/`。

权限配置解析失败时继续启动，但忽略全部无效自定义允许规则，保留内建硬限制和默认批准流程，并在 TUI 中显示包含配置来源和错误原因的警告。静默捕获被删除。

### 7. SubAgent Hook 是单层、异步、只读观察任务

`SubagentAction` 触发后立即排入后台执行，不阻塞触发它的 Turn。它强制使用 PLAN/只读权限，继承父 Provider，不能申请批准、调用 Agent/Team 工具或再次派发任何 Hook。完成、失败和取消都会形成用户可见通知，并可作为后续主 Agent Run 的 reminder。

该设计刻意不提供同步阻断、递归 Hook 链或独立模型选择；这些能力会让 Hook 变成第二套编排系统。

### 8. Memory 后台处理不拥有前台生命周期

Memory Extraction 在短锁内读取用户与项目记忆快照，释放锁后调用 LLM，返回后重新加锁。只应用仍基于原快照且未被显式操作改变的更新；冲突项跳过并记录，因此用户显式记忆操作优先。

Memory Governance 只读取最近 20 个 Session，并把序列化输入限制在 50,000 字符，超出时按最新优先丢弃旧内容。使用固定规则，不为不同模型引入 tokenizer 适配。

正常退出停止接收新提取并持续等待队列完成，同时显示等待状态；用户再次按 `Ctrl+C` 时取消当前提取和队列并继续退出。后台 Memory 不单独关闭共享 Provider。

### 9. Team Repository 是唯一事实来源

Team 只保留进程内和 tmux 后端。每次依赖跨进程状态的操作都从 Repository 加载最新快照并在一次操作内使用，不保存长生命周期可变 Team/lead 缓存。写操作仍通过 Repository 原子更新。

iTerm2 后端、自动检测和枚举分支全部删除。进程内成员继承父 Provider；tmux 子进程从持久化启动配置恢复等价的父 Provider 配置，不再接受每个成员的 model 覆盖。

### 10. 响应性优化保持固定和可测

流式文本先追加到 chunk 列表，以约 30ms 的固定周期合并并重绘；正常结束、取消和异常路径都立即刷新剩余内容。实现使用现有事件循环调度能力，不增加自适应刷新器。

质量门禁只扩展到本次新增、修改文件和明确的核心运行路径。mypy、C901、Ruff、架构测试和行为测试分别报告；不设置全仓覆盖率阈值，也不要求一次性清理无关历史错误。

## Risks / Trade-offs

- [Breaking 删除影响本地旧配置或导入] -> 在实现阶段先迁移仓库内调用方和测试，再删除符号；发布说明明确列出 `Task`、model 字段、iTerm2 和系统临时目录变化。
- [一次 change 涉及多个核心路径] -> 严格按阶段实施，每阶段保持测试通过并设置独立停止点，后续阶段不依赖未验收的半成品。
- [权限配置回退仍可能与用户原意不同] -> 回退时不应用任何自定义放行，保留内建限制，并让警告持续可见直到配置修复。
- [异步 Hook 结果到达较晚] -> 通知包含来源事件和执行状态，后续 reminder 只注入已完成结果，不改变已结束 Turn。
- [乐观 Memory 更新可能丢弃后台结果] -> 显式用户操作优先，冲突只记录不重试；需要重试时由下一次正常治理重新评估。
- [持续等待 Memory 会拖慢退出] -> 状态必须可见，并始终提供第二次 `Ctrl+C` 的显式强制退出通道。
- [30ms 刷新降低单 chunk 的即时性] -> 最终刷新无延迟；固定周期换取长流下稳定的事件循环响应。

## Migration Plan

1. 建立行为与架构保护测试，收紧 Workspace 和权限失败诊断，并删除旧兼容入口。
2. 提取 `ContextManager`、`ToolRunner`、`SessionService`，迁移真实调用链后删除旧 runtime seam。
3. 统一 Provider 生命周期，修正 Session 恢复、压缩事件和 token 锚点。
4. 实现受限 SubAgent Hook，修正 Memory 和 Team 状态行为。
5. 完成流式刷新、异步写盘和增量质量门禁，运行完整自动测试与 tmux 端到端验收。

每一步都可通过恢复到该阶段开始前的提交回滚；不进行持久化数据格式迁移。若旧 Session 无法恢复，保留原文件并显示错误，不原地重写。

## Open Questions

无。实现范围和关键行为已经逐项确认；具体私有 helper 名称由实现阶段在不扩大公共接口的前提下决定。
