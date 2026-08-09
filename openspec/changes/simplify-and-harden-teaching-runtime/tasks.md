## 1. 建立边界保护并删除公开噪声

- [x] 1.1 为真实 `CLI/TUI -> Agent` 调用链、Workspace 越界、Provider 关闭和 Session 恢复失败补充当前行为回归测试
- [x] 1.2 将文件访问判定收紧为规范化项目根目录包含检查，删除 `/tmp`、`/private/tmp` 例外并迁移对应测试
- [x] 1.3 让权限配置加载返回可诊断失败，接入“无自定义放行 + 内建默认策略 + TUI 警告”并覆盖有效/无效配置测试
- [x] 1.4 删除旧版 `Task` 别名、兼容门面、弃用分支和兼容测试，将仓库内调用方迁移到 Turn、Agent Run 或 Team Task
- [x] 1.5 删除 SubAgent 定义、Agent Tool 和 Team 派生链的 model 参数，并增加旧参数被明确拒绝的解析测试
- [x] 1.6 删除 iTerm2 Team 后端、检测逻辑、枚举分支和测试，验证自动选择只产生进程内或 tmux 后端

## 2. 深化 Agent 的上下文与工具模块

- [ ] 2.1 先以行为测试确定 ContextManager 的小接口，覆盖 Layer 1 卸载、Layer 2 摘要、溢出恢复和结果元数据
- [ ] 2.2 将上下文准备、摘要与恢复逻辑从 Agent 迁入 ContextManager，不新增单实现 Port 或转发门面
- [ ] 2.3 在 ContextManager 中修正 Compact Hook 触发顺序和 token 锚点重复累计，并增加 Layer 1 不触发 Hook 的回归测试
- [ ] 2.4 先以行为测试确定 ToolRunner 的小接口，覆盖调用分组、并发批次、权限、批准、Hook、取消和结果顺序
- [ ] 2.5 将完整工具执行事务从 Agent 迁入 ToolRunner，删除迁移后只转发或重复执行内部步骤的 Agent helper
- [ ] 2.6 将 Agent 收敛为 ReAct 与事件协调者，更新调用方及架构测试并确认上下文和工具行为保持一致

## 3. 深化 Session 模块并统一 Provider 生命周期

- [ ] 3.1 以创建、恢复、切换、记录和关闭行为测试确定 SessionService 接口，避免暴露 Writer 或恢复事务的中间步骤
- [ ] 3.2 将 Conversation、SessionWriter 和 Session 状态所有权从 NovaCodeApp 迁入 SessionService，保留 App 的 UI 状态与渲染职责
- [ ] 3.3 将 Session 恢复改为准备后一次提交的事务，并覆盖读取、压缩和 Writer 初始化失败时的完整回滚
- [ ] 3.4 将 Session 写入与 fsync 串行放入 `asyncio.to_thread()`，以慢 I/O 测试验证事件顺序、持久性和事件循环响应
- [ ] 3.5 为 Provider 窄接口和 OpenAI/Anthropic 实现增加异步 close，并以所有权测试约束借用者不得关闭 Provider
- [ ] 3.6 让主 Agent、普通 SubAgent、SubAgent Hook、进程内 Team 和 Memory Governance 共享应用拥有的 Provider，并验证切换或退出只关闭一次
- [ ] 3.7 迁移真实调用方后删除 SessionController、TurnEngine、runtime/ports、legacy adapter 及其浅层测试，并增加禁止重新引入的架构检查

## 4. 实现受限的 SubAgent Hook

- [ ] 4.1 用可控慢 Provider 测试定义 SubagentAction 的后台启动、非阻塞 Turn 和完成/失败/取消状态
- [ ] 4.2 用父 Provider 创建强制 PLAN/只读的 Hook Agent，裁剪写工具、批准、Agent 和 Team 派生能力
- [ ] 4.3 在 Hook Agent Run 内禁用所有 Hook 派发，并覆盖普通工具事件不会递归创建 SubagentAction
- [ ] 4.4 将后台结果转换为带来源的 Session 通知和后续 reminder，确保不改写已结束 Turn
- [ ] 4.5 在应用关闭时跟踪并收束 Hook 后台任务，验证任务异常不会泄漏或阻断主 Agent Run

## 5. 修正 Memory 自动化生命周期

- [ ] 5.1 为 Memory Extraction 增加“Provider 等待期间显式 manage_memory 不阻塞”和同条目冲突测试
- [ ] 5.2 将提取流程改为锁内快照、无锁 LLM、重新加锁应用未冲突操作，并记录而不重试冲突项
- [ ] 5.3 将 Memory Governance 输入限制为最近 20 个 Session 和最多 50,000 字符，增加最新优先的确定性裁剪测试
- [ ] 5.4 实现关闭时停止入队、持续等待和等待状态显示，并让第二次 Ctrl+C 取消当前提取及剩余队列
- [ ] 5.5 验证 Memory worker/governor 只借用共享 Provider，正常完成和强制取消路径都不单独关闭它

## 6. 让 Team Repository 成为唯一事实来源

- [ ] 6.1 为外部 tmux 更新后 lead 立即读取新成员和 Team Task 状态增加跨快照回归测试
- [ ] 6.2 删除长生命周期可变 Team/lead 缓存，让每次相关操作读取 Repository 最新快照并通过 Repository 原子写入
- [ ] 6.3 让进程内 Team 成员借用父 Provider，并覆盖成员创建不会产生新 Provider client
- [ ] 6.4 将父 Provider 配置持久化给 tmux 子进程，删除“选择 providers[0]”路径并验证多 Provider 配置下恢复正确

## 7. 改善响应性并建立增量门禁

- [ ] 7.1 用可控时钟和长 chunk 流测试定义约 30ms 合并刷新，以及完成、取消、异常时的剩余文本强制刷新
- [ ] 7.2 将 TUI 流式累计改为 chunk 列表和固定周期调度，删除逐 chunk 全量字符串复制与重绘
- [ ] 7.3 将严格 mypy 和 C901 检查扩展到本 change 修改文件及选定核心路径，并让 Ruff 覆盖相关脚本而不要求清理无关历史债务
- [ ] 7.4 运行 `uv lock --check`、Ruff、格式、增量 mypy、复杂度、架构测试和完整 pytest，分别记录结果并修复本 change 引入的回归
- [ ] 7.5 在 tmux 中启动 NovaCode，完成包含真实工具调用的 Turn，检查流式回复、Session 落盘、后台任务收束和退出行为
- [ ] 7.6 检查 `git diff --check`、OpenSpec 严格校验和 `docs/` 零变更；若随后提交，先按项目规则递增补丁版本并核对所有版本来源
