# 第五阶段验收报告

2026-10-09 完成 5.1–5.5，整体进度 27/71。统一压缩策略覆盖产品装配、自动/紧急/手动、Session 恢复、SubAgent、Skill fork、Hook 子 Agent 与两种 Team 后端。真实 tmux 中开启和关闭配置均完成代表问答；额外关闭组收到真实端点上下文超限，未摘要、丢轮或重试。这里只验证策略行为，不据三个机制样本估计模型收益或任务成功率。

## 实现与合同

| 任务 | 实现 | 验收依据 |
|---|---|---|
| 5.1 | `features.context_compression` 缺省 true，配置只接受布尔值；Agent 传入共享 `ContextManager.prepare()` | 老配置、三种触发入口、禁用时历史/锚点保持；相同窗口、工具 Schema、原始读取输出与预算的真实证据 |
| 5.2 | 禁用在 Layer 1 之前返回明确状态；紧急超限停止；手动抛出明确禁用结果，TUI 显示策略禁用；恢复不执行压缩 | 禁用时无 Provider 摘要、Hook、卸载文件或压缩事务；真实 `/compact` 禁用屏幕和端点超限账本 |
| 5.3 | 结果及事件分别记录摘要请求和历史接受，POST_COMPACT 包含 accepted；未接受摘要仍走同一个观测 Provider | 既有 Layer 1 无摘要 Hook、摘要 Hook 顺序和新增消息锚点测试；新增未接受摘要仍实测计入 300 Token 的机制测试 |
| 5.4 | SubAgent、Skill fork、Hook 角色和进程内 Team 继承父策略；Pane 启动配置及恢复显式校验 | 角色传播与 Team 两后端测试；原始长历史无改写恢复，旧摘要、卸载替换、压缩事务和截断/损坏历史被拒绝且原会话回滚 |
| 5.5 | 真实产品 App、正式 Agent/工具/权限/Session 与观测 Provider；固定 tmux 输入、隔离 HOME 和全新任务目录 | [最终复核](final/review.json)、各运行账本、屏幕及带指纹的 Session/工具产物；真实超限失败保持原记录 |

核心只接受布尔策略及可选观测回调，没有反向依赖 evaluation。主集 worker 增加显式压缩字段及策略元数据，但当前仍只接受 `eager-schema` 身份；临时开发可以单独关闭压缩，按需 Schema 和正式三配置尚未开放。没有实现第六阶段，也没有改变工具自己的输出预算、模型窗口或输出上限。

恢复检查使用已有持久化事务和摘要/卸载标记判定历史兼容性，不凭旧会话的缺省配置猜测原始历史。禁用组拒绝已经发生历史替换的来源；无压缩且完整的长历史可以原样恢复，后续运行若超限仍据真实错误终止。Pane 队员使用同一恢复校验，不能绕过 SessionService 的检查。新会话和评测重复均从空状态开始。

## 真实模型与 tmux

驱动为 [stage5_tmux.py](../../../../../evaluation/examples/stage5_tmux.py)，复核及原始字节归档为 [stage5_review.py](../../../../../evaluation/examples/stage5_review.py)。开启样本故意省略配置字段，核验兼容默认；关闭样本经配置解析得到 false。三份最终样本均使用 NovaCode 0.1.30、Anthropic、deepseek-v4-flash、thinking=true、SDK 零自动重试、请求超时 90 秒、输出上限 4096；依照既有适配器规则记录实际 thinking 条件。只注册六个基础工具，不启用记忆、子 Agent 或 Team，也不读取操作者真实记忆和指令。

相同的固定文件含 1100 行记录和末行 `FINAL-ID=ORBIT-731; VALUE=29`。第一请求要求只读、调用 read_file 后核对末行；接着固定 `/compact`，最后要求回答早期事实及是否允许写入。Bash 通过 DEFAULT 权限引擎的显式冻结规则授权；read_file 的原始返回超过 50,000 字节，三份 SHA-256 完全相同，业务文件在结束后逐字节未变。两种普通样本均正确回答事实并保持只读约束。关闭组出现一次可恢复工具错误，如实计入工具账本，不影响最终合同。

| 最终样本 | Provider 请求 | 工具请求 | Token | 卸载 | 摘要请求/接受 | 终止 |
|---|---:|---:|---|---:|---|---|
| [开启](final/on/result.json) | 5 | 2 | 完整实测 20,316 | 1 | 1/1，手动 | completed，两个请求完成 |
| [关闭](final/off/result.json) | 5 | 4 | 完整实测 134,403 | 0 | 0/0 | completed，两个请求完成 |
| [关闭后新增超限资料](final/overflow/result.json) | 5 | 4 | 已知下界 68,651，另 1 次未知 | 0 | 0/0 | context-overflow，第一请求完成 |

三份样本的原始输入、工具能力/读取边界、窗口、输出和预算一致；超限样本的第二请求按事前脚本增加 `" x"` 重复 1,100,000 次的资料，第一请求及手动禁用检查均已完成。服务端返回 HTTP 400：最大上下文 1,048,576 Token，本次请求 1,125,715（消息 1,121,619＋输出余量 4096）。这是一段先合法、后累积超限的机制轨迹，不把初始非法输入用作两组收益比较。产品本地窗口保持 200,000；本阶段没有把这个本地默认值改成端点窗口，完整窗口身份核验仍属于先导阶段。

超限后的请求没有可信 usage，因此记未知，停止追加工作；不能把其成本记零。原始历史包含全部追加资料，没有摘要事务或紧急重试；三个最终运行均退出 0、Session 写入完成、实际 Provider close 一次，清理 passed。关闭组手动禁用可见于 [手动命令屏幕](final/off/manual-pane.txt.gz)，超限原文可见于 [超限屏幕](final/overflow/tmux-pane.txt.gz)。自动/紧急入口及未接受摘要的机制负例由测试补充，不将 Fake 结果写成真实模型效果。

每次有限上限为 240 秒、1,400,000 Token、12 Provider 请求、12 工具请求，单请求预留上限 900,000，清理宽限 15 秒；三份最终样本的总预留上限为 900 秒、4,200,000 Token、36 Provider 请求和 36 工具请求。跨进程 [Campaign 预留](final/campaign.jsonl) 在调用前执行，未知用量和失败预留不退还。较大的 Token 预留用于真实超限探测，不能解释成实测费用。归一化沿用第四阶段实际缓存字段探针，规则绑定原探针字节指纹，不假设协议名称就足以解释缓存。

三个单次机制样本不是配对正式实验；两份普通样本的 Token 差异同时包含随机模型路径、工具恢复和手动摘要，不据此给出压缩收益百分比。该专项驱动没有记录正式主端到端的完整分段，`phase_seconds` 中未测量项保持 null；总墙钟与关闭时间只供过程审计，不用于性能比较。没有可信价格或账单，本报告不计算金额。

## 调试记录、质量和停止范围

[调试汇总](debug-summary.json)及 `debug/` 保留六份先前尝试，不覆盖原运行。首个启动因探针指纹函数接收 bytes 而报错，未调用模型；一次真实运行等待未预授权的 Bash 后停止，随后在新的任务配置中冻结授权；旧 fixture 的低字符/Token 比例导致实际用量超过请求预留估计，关闭与超限尝试按既有预算规则停止。后续将公共文件改为低分词密度的固定文本，两组同时使用新 fixture，相同上限未放宽；旧停止成本继续保留。

调试账本已知 Token 合计 138,687，最终三份已知下界合计 223,370，本阶段已取得账本的合计下界为 362,057；最终超限请求另有 1 次未知。下界不是完整账单。导出文件和压缩归档经过真实 API key 字节扫描，无凭证落盘。屏幕 `.gz` 与 `artifacts.tar.gz` 保留原始字节，解包到对应账本目录后可读取 `artifacts/<SHA-256>.txt`；每份归档另有原始产物指纹清单。

完整 pytest 为 [862 passed](pytest.txt)，包含 14 个新增策略测试及 Team 传播断言；[Ruff](lint.txt)、[格式](format.txt)、[严格评测 mypy](mypy-evaluation.txt)、[评测 C901](complexity-evaluation.txt)、[运行边界/增量门禁](boundaries.txt)、[锁文件](lock.txt)和启动检查通过。增量 mypy 180→179、C901 21→20，无新增诊断。首次全量测试因 PATH 缺少虚拟环境中的 mypy/ruff/python 而有四个失败，保留 [原日志](debug-pytest-path.txt.gz)；使用正确项目环境重新完整运行通过，没有为环境错误改写业务代码。

版本由 0.1.29 递增到 0.1.30，pyproject、运行时、uv.lock 和安装 metadata [一致](version-identity.json)；uv.lock 仅更新自身版本。交付源码字节指纹见 [source-identity.json](source-identity.json)，这是交付快照，不冒充正式冻结身份。质量命令及退出码见 [quality-check.py](quality-check.py)和 [quality-results.json](quality-results.json)。OpenSpec 严格校验通过。

复跑命令为 `.venv/bin/python evaluation/examples/stage5_tmux.py --root <全新外部目录>/runs/on --mode on`，同一命名空间再使用 off 与 overflow。该命令会发起有界真实模型调用；已有运行拒绝覆盖。只读复核命令为 `.venv/bin/python evaluation/examples/stage5_review.py --root <外部目录> --output <全新证据目录>`。模型凭证只从既有配置在进程内读取，命令行与证据均不包含认证秘密。

本轮仅完成第五阶段并停止后续实施。未实现按需 Schema、未建设开发/冻结题库、未执行正式实验，也未取得本轮 Windows 本地验收；第六阶段和第十二阶段保持未完成。没有读取、审查或修改项目 docs/。按用户要求仅提交推送本阶段实现、测试、证据、任务进度和版本文件。
