## Why

NovaCode 已具备 Agent、Team、Hook、Memory、权限和会话恢复等教学能力，但真实运行链与未使用的抽象并存，部分后台流程还存在长持锁、资源未关闭、状态缓存陈旧和事件循环阻塞等问题。作为教学项目，本次调整优先删除无效复杂度，并修正能够破坏核心行为或掩盖错误的缺陷，而不是按生产系统继续扩建基础设施。

## What Changes

- **BREAKING** 删除未进入真实运行链的 `SessionController`、`TurnEngine`、`runtime/ports`、legacy adapter，以及语义模糊的旧版 `Task` 兼容接口。
- 将真实运行链保持为 `TUI -> Agent`，并把上下文管理、工具执行和 Session 事务分别收敛到具有具体职责的深模块；不恢复通用 Ports/Adapters 层。
- **BREAKING** Workspace 文件访问范围仅包含项目根目录及其子目录，不再默认放行系统临时目录；损坏的权限配置回退到保守默认值并显示明确警告。
- **BREAKING** SubAgent 和进程内 Team 成员统一继承父 Provider，删除未生效的模型选择参数；Provider 由应用共享并在切换或退出时只关闭一次。
- 完整实现异步、只读、不可递归的 SubAgent Hook，并通过通知或后续 reminder 返回结果。
- 修复上下文压缩事件、token 锚点和 Session 恢复回滚语义；Compact Hook 只围绕真正的历史摘要压缩触发。
- 修复 Memory Extraction 的网络等待长持锁问题，限制 Memory Governance 输入规模，并提供“正常等待、再次中断强制退出”的关闭行为。
- **BREAKING** Team 仅保留进程内和 tmux 后端，删除 iTerm2 后端；Repository 成为跨进程协作状态的唯一事实来源。
- 对流式 UI 刷新和 Session 写盘进行有界优化，并对新增、修改及核心路径采用增量类型、复杂度和行为测试门禁。

## Capabilities

### New Capabilities

- `teaching-runtime-orchestration`: 真实 Agent 运行链、上下文压缩、工具执行、Session 事务和 Provider 生命周期的可观察行为。
- `workspace-permission-safety`: Workspace 文件边界、权限配置失败回退和用户可见诊断。
- `background-agent-hooks`: SubAgent Hook 的异步执行、只读限制、防递归及结果交付行为。
- `memory-automation-lifecycle`: Memory Extraction、Memory Governance 输入预算、冲突处理和关闭语义。
- `team-collaboration-state`: Team 后端范围以及 Repository 单一事实来源要求。
- `runtime-responsiveness-quality`: 流式 UI、异步写盘和增量质量门禁的验收要求。

### Modified Capabilities

- `runtime-boundaries`: 归档时接续此前的 `stabilize-runtime-boundaries` 主规格，显式移除 Turn Engine、Session Controller、旧兼容门面和阶段性迁移接缝的 4 条要求；保留 Agent Run / Team Task 术语、依赖方向和类型化错误要求。

## Impact

- 主要影响 `src/novacode/agent/`、`context/`、`permission/`、`memory/`、`subagent/`、`team/`、`tui/`、`llm/`、`runtime/` 和会话持久化相关代码。
- 需要删除或迁移依赖旧 runtime seam、`Task` 兼容名称、SubAgent model 字段及 iTerm2 后端的测试与配置示例。
- 需要新增主运行链、恢复回滚、权限边界、Provider 关闭、Hook、Memory 并发、Team 快照和长流响应测试，并执行 tmux 真实对话验收。
- 不新增第三方依赖，不引入资源池、动态模型路由、临时目录注册系统或通用依赖注入框架。
