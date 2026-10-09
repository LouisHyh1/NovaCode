# 第三阶段验收报告

2026-10-09 完成 3.1–3.7，整体进度 16/71。本阶段交付 Provider 边界观测、协议原始用量、SDK 请求控制、工具审计、预算准入与可重建文件账本。真实模型调用数为零；第四阶段入口、容器隔离、所属进程清理与外部独立判题尚未实施，也未取得 CODE_READY、PILOT_COMPLETE 或任何任务成功率结论。

## 实现与验收对应

| 任务 | 实现 | 验证 |
|---|---|---|
| 3.1 | `evaluation/observation.py` 的 ObservedProvider；Request 显式 role、logical_call_id、attempt；主请求紧急恢复和 Layer 2 重试共享逻辑身份 | 主请求与不消费 usage 的摘要合计记一次；相同 usage 重发不会双计；摘要重试关联原请求；错误事件、迭代异常、同步流创建失败、提前关闭与取消保留原错误类型 |
| 3.2 | llm.Usage 保留白名单 raw、protocol、适配器建议规则；`evaluation/usage.py` 要求外部传入 UsageRule 与核验依据 SHA-256 | 缓存独立、缓存为输入子集、推理为输出子集及独立推理量分别验证；缺字段、无规则和协议不符保持未知；已知部分下界、完整实测与准入估计分列 |
| 3.3 | ProviderConfig 支持可选 max_retries、timeout、max_output_tokens；适配器记录 SDK 版本、明确设置或 SDK 默认、输出限制和实际 thinking 条件 | 本机真实 SDK + MockTransport 的 HTTP 500 每逻辑请求只有一次 HTTP 尝试；Anthropic/OpenAI SSE 用量与工具增量实测解析通过；普通默认仍为 SDK 的两次自动重试；有工具历史不发送 thinking，与 HTTP 请求体一致 |
| 3.4 | ToolEvent 增补 call_id、独立 invocation_id、序号、完整脱敏参数、单调时间、授权/执行/错误与 retry_of；ToolRunner 可接收同步观测回调 | 同名只读工具仍并发且分别关联；超过 80 字符业务参数保留；发现、错误、权限/Hook 拒绝、未知工具和重试分别记录；START 一次计数、END 仅关联；取消批次回收工具任务并保留 END |
| 3.5 | `evaluation/budget.py` 的有限 BudgetLimits、BudgetPolicy、RunBudget；共享类别与 Campaign 总量，先导可叠加临时上限 | 首个调用前拒绝缺少/无效预算、SDK 零重试、超时或输出上限；请求按输入估计加输出上限预留，考虑未结算并发预留；主/摘要/发现均收费；超额实测保留并停止追加，未知用量也停止；三配置无单组覆盖参数 |
| 3.6 | `evaluation/ledger.py` 的独占创建、序号、UTC/单调时间、flush/fsync、事件和产物 SHA-256、phase 与只读 rebuild；用量收到时立即落盘 | 在 request_end 前能重建已知用量且结束后不双计；无用量的未结束请求保持未知；尾部截断标记并保留原字节，内部损坏拒绝；准备、初始化、Agent、清理、判题和主端到端分列；未观测时间为 null；凭证、嵌套认证字段和 JSON 文本中的秘密不落账本/产物 |
| 3.7 | ObservedProvider 是共享资源唯一所有者，借用句柄 close 不释放资源，所有者用共享关闭 Task 保证实际关闭一次 | 多借用者、并发 close、关闭失败和取消等待均只执行一次底层 close；原流错误优先，关闭错误单独记录；摘要消费端补齐 finally，避免重试开始时上次失败账本尚未结算 |

产品核心只接收 Provider 与 ToolEvent 回调，没有导入 evaluation，也没有新增执行引擎。新增 privacy.py 纳入增量质量门禁；基线仅新增检查路径，没有新增或放宽历史诊断。OpenAI 流式解析拆成参数构造与工具增量辅助方法，并在 finally 关闭 SDK 响应，新增真实 SDK SSE 测试覆盖这一调整。

## 质量验证与版本

本阶段新增 45 个测试；完整 [pytest](pytest.txt) 为 823 passed。[Ruff lint](lint.txt)、[格式](format.txt)、[新增模块严格 mypy](mypy-evaluation.txt)、[新增模块 C901](complexity-evaluation.txt) 和 [OpenSpec 严格校验](openspec.txt) 均通过。[增量边界门禁](boundaries.txt) 为 mypy 180→179、C901 21→20，新增诊断均为零。

可复跑的全部本地验证命令、原索引环境覆盖与退出码见 [quality-results.json](quality-results.json)，驱动脚本为 [quality-check.py](quality-check.py)。[锁文件](lock.txt)、[版本启动](startup-version.txt) 与 [帮助启动](startup-help.txt) 通过。项目由 0.1.27 递增到 0.1.28，pyproject、运行时、uv.lock 和安装 metadata 完全一致，见 [version-identity.json](version-identity.json)。uv 生成锁文件后保留原清华索引与全部依赖版本，Git 差异仅 NovaCode 自身版本。

本机验证 SDK 为 Anthropic 0.112.0、OpenAI 2.44.0、httpx 0.28.1。Context7 查询用于核对零自动重试和有限超时用法；实际参数结论以本机 SDK、模拟 HTTP 请求次数和 SSE 解析证据为准，不把更新的上游依赖迁移建议应用到当前锁文件。[Anthropic 重试说明](https://github.com/anthropics/anthropic-sdk-python#retries)，[OpenAI 重试说明](https://github.com/openai/openai-python#retries)。

## tmux 产品链路

最终 0.1.28 验收使用 [tmux_observation_probe.py](../../../../../evaluation/examples/tmux_observation_probe.py)，在全新临时工作区启动正式 NovaCodeApp、Agent、默认工具、DEFAULT 权限和 SessionService；用户目录与技能目录指向隔离临时位置，未载入真实用户记忆、规则或技能。驱动脚本 [tmux-driver.py](tmux-driver.py) 接收新的证据目录以便复跑，已有账本拒绝覆盖。

实际输入为“请读取 observation-one.txt 和 observation-two.txt，分别报告结果，并说明调用关联是否正确。”两个 read_file 在正式 ToolRunner 中执行并返回各自结果，TUI 显示回复，随后输入 /compact 触发正式 Layer 2。账本包含两次主请求和一次摘要，三个模拟用量各为 160 Token，重建合计 480；工具调用数和成功数均为 2、用量缺失数为 0，Provider 实际关闭一次，Ctrl+C 后 pane 退出码为 0。主端到端约 1.329 秒，准备和独立判题未执行，保持 null。

最终证据为 [账本](tmux-release/tmux-ledger.jsonl)、[屏幕](tmux-release/tmux-pane.txt)、[Session](tmux-release/tmux-session.jsonl)、[核验结果](tmux-release/tmux-verification.json) 及账本引用的输出产物。手动摘要本次没有降低上下文长度，TUI 如实提示该结果，摘要请求和费用仍完整记录。此前 0.1.27 的首次与进一步隔离检查记录保留，最终结论以上述 0.1.28 证据为准。

## 验证边界与后续接入

自动入口在第四阶段实现。本阶段只在测试与 tmux 示例的装配端包裹 Provider，并传入 tool_observer；普通 CLI 不会默认生成评测账本，也不改变 SDK 原默认。评测装配必须把相同借用实例交给 Agent 与上下文路径，由外围所有者最后 close，并将已知认证值显式传给 Ledger 的 secrets。

UsageRule 的 SHA-256 是证据绑定字段，机制 fixture 的摘要不构成 DeepSeek 或其他兼容端点的真实语义核验。未提供规则时 raw 保留、归一化总量未知；错误缺失用量会阻止后续摘要重试，这是预算的保守停止行为。SDK 已禁用隐藏重发不代表服务器内部处理次数已知。本阶段不计算金额，也没有真实费用结论。

工具重试使用 `same-arguments-after-error-v1`：同一 Runner 中同名且规范 JSON 参数指纹相同的后续请求，关联最近失败的 invocation_id；这是明确标注的过程诊断规则，不推断模型意图。同名不同参数不关联为重试；取消或事务异常保留 interrupted 状态，不伪造工具成功。模型本身的 call_id 可以重用，账本以新 invocation_id 区分请求。

账本是单进程单写入者，支持中断后的只读重建，不在原文件上恢复追加。时间相对该进程的单调时钟，跨进程比较依据 UTC 和各段时长；未观测段保持未知。输入估计包含协议无关请求、系统块、reminder 和 Schema，仅用于准入，不声称等于计费 Token。类别数值冻结、Campaign 恢复、工具/子进程超时后的完整清理与独立判题仍由后续阶段装配并验收。

本轮初始工作区干净。交付仅包含第三阶段实现、测试、示例、证据、进度、质量范围扩展与版本文件；没有读取、修改或依据项目 docs/，没有修改长期记忆。交付文件的字节指纹见 [delivery-fingerprints.sha256](delivery-fingerprints.sha256)。
