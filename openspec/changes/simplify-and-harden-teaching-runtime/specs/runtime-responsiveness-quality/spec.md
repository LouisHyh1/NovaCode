## ADDED Requirements

### Requirement: 流式 UI 合并刷新
TUI SHALL 使用 chunk 集合累计流式文本，并 SHALL 以约 30ms 的固定周期合并和重绘，而不是为每个 Provider chunk 重建全部累计内容。

#### Scenario: 高频长文本流
- **WHEN** Provider 在一个刷新周期内产生多个文本 chunk
- **THEN** TUI 将它们合并为一次周期刷新且最终文本顺序和内容保持完整

### Requirement: 所有终止路径刷新剩余文本
流式回复正常完成、取消或失败时，TUI SHALL 在展示终止状态前立即刷新尚未绘制的剩余 chunk。

#### Scenario: 刷新周期前发生取消
- **WHEN** 用户在待刷新 chunk 尚未达到 30ms 周期时取消 Agent Run
- **THEN** TUI 先展示全部已接收文本，再展示取消状态

#### Scenario: 流式响应抛出异常
- **WHEN** Provider 在存在待刷新文本时失败
- **THEN** TUI 保留全部已接收文本并随后展示错误状态

### Requirement: Session 写盘不阻塞事件循环
SessionService SHALL 串行保持事件顺序和现有 `fsync` 持久性语义，并 SHALL 通过 `asyncio.to_thread()` 执行阻塞文件写入与同步。

#### Scenario: 慢速磁盘写入
- **WHEN** Session 文件写入或 fsync 暂时阻塞
- **THEN** 事件循环仍可处理流式 UI 更新和取消信号，且事件最终按原顺序落盘

### Requirement: 增量质量门禁覆盖变更与核心路径
CI 和本地验证 SHALL 对本次新增或修改文件以及明确的核心运行路径执行 Ruff、格式、严格 mypy、复杂度和行为测试门禁，并 SHALL NOT 以清零无关历史债务作为本 change 的完成条件。

#### Scenario: 修改核心运行文件
- **WHEN** change 修改 Agent、上下文、工具、Session、权限、Hook、Memory、Team、Provider 或 TUI 核心路径
- **THEN** 对应文件进入类型、复杂度和相关行为测试门禁

#### Scenario: 无关历史 mypy 错误仍存在
- **WHEN** 未修改且不在选定核心路径的旧模块仍有既有类型错误
- **THEN** 该历史错误被记录但不阻止本 change 验收

### Requirement: 真实链路端到端验收
实现完成后系统 SHALL 在 tmux 中启动 NovaCode，提交真实对话请求，并 SHALL 验证工具调用、流式回复、Session 写入和关闭行为满足本 change 的明确标准。

#### Scenario: tmux 真实对话
- **WHEN** 验收人员在 tmux 中完成包含至少一次工具调用的真实 Turn 并退出 NovaCode
- **THEN** 工具结果、最终回复、Session 记录和资源关闭均可观察且无挂起
