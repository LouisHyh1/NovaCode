# 第四阶段验收报告

2026-10-09 完成 4.1–4.6，整体进度 22/71。交付独立自动化入口、正式产品装配复用、容器隔离、冻结授权、预算停止、所属进程清理、完整提交提取和外部官方判题。最终同配置 tmux 严格成功；自动化提交通过官方验收但达到 Token 预算，strict_success=false。这是运行器及一个代表候选的开发验收，不是正式评测成绩或成功率估计。

## 实现及验证

| 任务 | 实现 | 本轮证据 |
|---|---|---|
| 4.1 | `assembly.assemble_agent` 共用 Agent、ContextManager、ToolRunner、SessionService；`python -m novacode.evaluation` 编排外部容器，worker 直接消费 `Agent.run()` | 架构门禁与产品 TUI 回归通过；没有新增执行引擎或核心反向导入 evaluation |
| 4.2 | 每次新建固定 digest 容器，工作树检查 base/干净状态；独立 HOME、Session、对话和 `.novacode`；不注册 Memory、Team 或子 Agent | 重复机制测试、两入口不同 Session、六工具 Schema 指纹、容器无主机挂载；旧内部状态被拒绝 |
| 4.3 | DEFAULT 模式，冻结 allow/deny 经正式规则引擎；Ask 回调确定性返回 DENY_ONCE；允许集合在 Agent/ToolRunner 生效 | Ask 不修改文件且计拒绝；Plan、黑名单、越界拒绝；正式 PRE/POST_TOOL_USE Hook 派发；最终两入口相同 Write/Edit/Bash 预授权 |
| 4.4 | 时间/调用/Token 预算；关闭 Session、Hook、Provider；停止和确认容器后提取，再清理容器；临时 Git 索引相对固定 base 包含已提交、工作树及新增文件 | 慢 Bash 超时后进程消失；真实残留 sleep 在 shell 返回后仍存活，容器停止后全部消失；提交/新文件/忽略文件/安装基线负例通过；两个真实提交从停止后快照逐字节重建 |
| 4.5 | 外部 argv 判题进程，隐藏资产逐项校验；独立干净官方容器只接收提交和隐藏验收；每项条件、resolved、strict 分离 | 完成但错误、超时/预算但补丁正确、范围越界、清理失败、缺判题项及无法判题均可区分；两份最终官方报告保存逐测试结果 |
| 4.6 | 固定 Faker 代表候选、真实 DeepSeek、零 SDK 重试、有界先导；tmux 正式产品 App 使用相同 Provider 和 Agent 装配 | 自动化与 tmux 的公开请求、运行包、模型参数、权限、允许工具与任务资产一致；最终提交均 resolved=true，终止状态如实不同 |

新增 25 项机制测试在 [test_runner.py](../../../../../tests/evaluation/test_runner.py)，覆盖正式工具/Hook、重复隔离、预算、Git 产物、判题和 TUI 启动。脚本化 Provider 的结果只证明机制；真实模型证据如下单列。

## 最终真实模型与 tmux

代表题为 `joke2k__faker-2096`，base 为 `4240ef8fc783c0c1598085b596783ace9ccaee03`，数据 revision 为 `b51a86422e10cfd403beb4773e5a2947953e36ec`。第 1 阶段已核验原始失败和参考补丁连续三次成功；本轮仍使用相同固定镜像和官方判题 commit `ad79b850f15e33992e96f03f6e97f05ddf9aa0be`。这里只使用这一代表候选，没有提前宣称第 8 阶段题库已入库。

两入口最终使用 `stage4-final-2` 的同一运行包、任务合同和 pilot 配置，见 [task.json](final/task.json)、[环境身份](final/public/environment.json)、[pilot.json](final/pilot.json)。Agent 独立 Python 3.12 环境位于 `/opt/novacode/venv`；工具仍在 `/testbed` 使用任务镜像原解释器和依赖。运行时没有注入目标测试名单、参考补丁、原始数据行、操作者指令或长期记忆。判题只在运行停止后进行，结果不反馈给该次 Agent。

| 最终入口 | Provider 请求 | 工具请求 | 实测 Token | 终止 | 官方 resolved | strict_success |
|---|---:|---:|---:|---|---|---|
| [自动化](final/runs/auto-1/result.json) | 13 | 18 | 274,992 | budget-exhausted | true | false |
| [tmux](final/runs/tui-1/result.json) | 13 | 13 | 238,873 | completed | true | true |

两个最终运行都无未知 usage、工具拒绝或工具错误；修改范围和清理均通过。自动化在追加请求前预留输入估计及 4096 输出余量，剩余 Token 不足时停止，没有追加免费调用或事后扩大该次预算。模型已生成可通过判题的补丁并不使其自然完成，因此严格成功仍为 false。这是已通过验收的停止语义，不将该结果改写为成功。

tmux 记录见 [屏幕](final/runs/tui-1/tmux-pane.txt.gz)、[退出码](final/runs/tui-1/tui-exited)、[worker 账本](final/runs/tui-1/worker/ledger.jsonl) 和 [外围账本](final/runs/tui-1/orchestration.jsonl)。Session 内容以带 SHA-256 的账本产物保存；最终返回修复原因和测试结果，退出码 0，真实 Provider 实际 close 一次。相同请求在产品输入框中的 CRLF 被规范化比较，交给 Agent 的仍是原始公开请求。现行驱动等待 TUI 初始化后才粘贴固定请求，使用 bracketed paste 保留换行；首次提前粘贴被拒绝，重新粘贴同一固定请求，没有增加提示或反馈答案。

外围主端到端时间从初始化到容器资源关闭，覆盖 SDK 初始化、脚本、工具和资源收尾；准备和独立判题分别保存。worker 内的组件装配、Agent、关闭时间用于细分诊断。tmux 的约 112.6 秒包含界面准备及固定输入重新粘贴等待，不用它与自动化约 38.7 秒的 worker 时间做效率比较。真实模型最终文本不要求逐字一致。

两个停止后快照经当前安全 Git 提取器重建，与提交补丁逐字节一致，见 [自动化产物复核](final/runs/auto-1/artifact-review.json) 和 [tmux 产物复核](final/runs/tui-1/artifact-review.json)。主机只复用 Git 对象，不执行 Agent 可改写的 Git 配置、Hook 或 filter；新增忽略文件仍包含在提交中。镜像中原有忽略文件仅在指纹完全不变时排除，改变后仍计修改范围。`.novacode`、Python/pytest 缓存单独处理，完整停止后快照保留在外部执行缓存，未把任务源码与安装环境复制进 Git。

## 有限预算及真实用量规则

最终代表验收每次上限为 240 秒、300,000 Token、16 Provider 请求、30 工具请求，单请求余量上限 40,000 Token，清理宽限 20 秒。最终两入口 Campaign 总上限为 600 秒、600,000 Token、32 Provider 请求和 60 工具请求。跨进程 flock 账本在开始前预留整次上限，失败和未知用量不退还，不允许同一账本更改总上限；文件父目录为 Campaign 运行命名空间。正式三配置及正式类别预算尚未开放。

SDK 配置固定 Anthropic、deepseek-v4-flash、thinking=true、max_retries=0、timeout=60、max_output_tokens=4096。请求账本记录 SDK 版本和实际 thinking 条件：无工具历史发送思考，存在工具历史按既有产品规则不发送。普通产品默认不变。[官方兼容说明](https://api-docs.deepseek.com/zh-cn/guides/anthropic_api/)说明 budget_tokens 会被忽略，2048 仅是适配器发送的值，不声称服务器执行了该思考预算。

首个代表运行前，以独立 [有限探针预算](usage-probe/budget.json) 执行两次 Anthropic 和一次原生用量对照，原始响应见 [usage.json](usage-probe/usage.json)。相同公开内容第一次 Anthropic input=1609/cache_read=0，第二次 input=201/cache_read=1408；原生 prompt=1609/cache_hit=1408/cache_miss=201，总量 1611。由此核验当前端点的缓存独立计入规则，规则绑定原始证据 SHA-256；不无条件套用协议名称，也不将缺失字段记零。探针原生返回模型标识 deepseek-flash，Anthropic 返回 deepseek-v4-flash，两者身份分别保留。当前任务结果依据配置的 Anthropic 端点，不将探针当作正式多配置实验或真实账单。

## 调试失败与审计边界

所有调试结果按新身份保存，未覆盖原账本。`debug/` 保留容器解包前置失败和两条预算终止：94,234 与 137,448 Token；相对授权未匹配绝对路径和安装/pytest 产物混入问题由真实轨迹定位后修复。开发预算修订记录在 pilot-v1 与 pilot 中，修订同时用于两入口，未调整冻结任务或正式实验。

`release-debug/` 保留一次正常完成的自动化运行，消耗 274,549 Token，但当时判题适配器漏传 instance_id，原记录 acceptance=ungradable、strict=false。修正后从已保存的独立测试日志重新调用固定官方解析器，单独 [grading-review.json](release-debug/runs/auto-1/grading-review.json) 确认 resolved=true、目标及回归均通过；原运行未改写，未重新调用模型，未把复核混作新成功样本。

`startup-debug/` 保存 Textual 样式异常与修复前的 tmux 记录。子类相对 CSS 路径会按定义模块定位，[Textual 官方文档](https://github.com/textualize/textual/blob/main/docs/guide/app.md)与本机异常一致；现在显式引用产品样式绝对路径，启动和固定请求传递由新增 TUI 机制测试验证。另一次驱动退出与清理竞争导致 worker 账本未导出，原 cleanup=failed/ungradable 保留；当前驱动即使失败也等待外围 runner 收尾。缺账本样本不冒充完整成本记录。

五份已导出的真实 Agent 账本共 55 次请求、1,020,096 Token，探针另有 3 次请求、4,833 Token；调试和失败费用未删去。上述合计明确限定为已取得账本，不宣称所有异常运行的精确完整成本，也不计算缺少可信价格/账单规则的金额。没有执行完整先导、正式冻结或 432 个计划运行。

## 质量、复跑及停止范围

完整 pytest 为 [848 passed](pytest.txt)，新增 25 项机制测试全部通过；[lint](lint.txt)、[format](format.txt)、[严格 mypy](mypy-evaluation.txt)、[C901](complexity-evaluation.txt)、[架构及增量门禁](boundaries.txt)、[锁文件](lock.txt)和启动检查均通过。增量门禁 mypy 180→179、C901 21→20，无新增诊断；只增加 assembly 检查路径，没有放宽历史诊断。可复跑验证命令与退出码见 [quality-check.py](quality-check.py) 和 [quality-results.json](quality-results.json)。残留进程独立检查见 [process-probe](process-probe/ledger.jsonl)，模型调用数为零。

版本由 0.1.28 递增至 0.1.29，pyproject、运行时、uv.lock 和安装 metadata 一致，见 [version-identity.json](version-identity.json)；锁文件仅更新 NovaCode 自身版本。两入口实际运行包和当前交付源码分别有字节指纹，正式冻结前仍须重新记录届时的完整执行身份。未取得 Windows 本地验收；第 12 阶段保持未完成。

代表任务驱动为 [stage4_live.py](../../../../../evaluation/examples/stage4_live.py)，官方判题适配为 [stage4_judge.py](../../../../../evaluation/preflight/stage4_judge.py)。从项目根使用 `.venv/bin/python evaluation/examples/stage4_live.py --setup --root <全新外部目录> --usage <探针证据目录>` 生成公开合同、隐藏资产及独立运行包，再使用 `--root <目录> --run <新运行ID>`；tmux 增加 `--tui`。真实运行使用项目配置中约定的 Provider，但凭证只经 stdin 或启动前删除的 tmpfs 输入注入，不进入命令参数、证据或公开任务包。复跑不是纯读操作，会创建有界真实调用；已有运行和账本拒绝覆盖。

本轮只完成第四阶段，未实现压缩禁用或按需 Schema，未建设后续题库、运行开发集校准、冻结或正式实验。主集当前仅支持 `eager-schema` 的产品兼容策略；其他配置显式拒绝。没有读取、审查或修改项目 docs/，没有修改真实用户记忆。按本轮要求验证后停止实施，并仅提交推送本阶段实现、测试、证据、任务进度和版本文件。

原始工具文本按对应运行的 `artifacts.tar.gz` 保存，解包到该账本父目录可恢复 `artifacts/<SHA-256>.txt` 引用；提交补丁、独立判题原文和终端屏幕以 `.gz` 保存，解压字节与原文件完全一致。账本、JSON 结果及指纹不因归档改写；不去除原始尾空格、制表符或补丁上下文空行。
