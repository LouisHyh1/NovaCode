# 第六阶段验收报告

2026-10-10 完成 6.1–6.6，整体进度 33/71。产品默认全量 Schema，显式 `features.progressive_tool_schema: true` 开启按需暴露；正式 Agent、工具执行、权限、Hook、Session、辅助角色与两种 Team 后端均传递同一策略。真实模型自动入口和 tmux 均完成发现后使用内置及本地 MCP 工具的两轮请求。本阶段只证明机制与入口行为，不估计按需 Schema 的收益或成功率。

## 实现与验收

| 任务 | 实现 | 证据 |
|---|---|---|
| 6.1 | 配置缺省 false 且仅接受布尔值；相同 Registry 和预先连接的 MCP；评测支持 full、no-compression、eager-schema | 老配置及三配置允许集合测试；所有入口保留六个基础工具和同一发现入口 |
| 6.2 | `ToolExposure` 属于各 Agent；NFKC/casefold 精确名称优先，职责词项全部匹配、名称排序；最多 5 项和 32,000 字符 Schema，超大 Schema 不截断曝光 | 无匹配、相近职责、有界结果、重复发现、超大定义与独立状态测试；业务工具执行数保持零 |
| 6.3 | 每轮生成定义；发现返回完整 Schema 并加入下一轮；固定多轮保留，独立重复重置 | 正式 Agent 请求序列测试；真实请求列表、Schema 指纹及完整脱敏输入产物 |
| 6.4 | 发现只检索当前允许集合与 Plan 过滤后的清单；工具调用仍走权限和前后 Hook；同一批请求不能因发现并发而执行原本未曝光的工具 | 角色限制、权限拒绝、Hook 阻断、Plan 写入拒绝、未知工具及猜测调用测试；调试 tmux 的两个拒绝及恢复记录 |
| 6.5 | 指纹绑定策略、角色、模式、允许集合和合格工具 Schema；Session 内目录保存曝光状态，兼容恢复保留，变化/损坏重建并给出提醒；新会话重置 | 真实摘要事务后曝光保留、Session 切换/恢复、坏缓存、Schema/角色/策略变化及所有辅助角色传播测试 |
| 6.6 | 固定本地 CI 记录，同一允许集合和有限预算，内置工具与真实 stdio MCP 注册 | [最终复核](final/review.json)、两入口原始账本、请求/工具/Session 产物及 [tmux 屏幕](final/tmux-final/tmux-pane.txt.gz) |

基础集合是 read_file、write_file、edit_file、bash、glob、grep；`discover_tools` 是各 Agent 私有执行入口，不写入共享 Registry，也不会建立新 MCP 连接。角色/Plan 仍可进一步限制基础工具；发现不能授予业务操作权限。按需请求的目录仅含名称与最多 160 字符职责，最多列 64 项并标明截断；检索仍覆盖全部合格工具。目录和发现返回均进入实际模型输入及 Token 账本，完整定义不藏入固定提示。

曝光保存于 `.novacode/sessions/<session-id>/exposure.json`，使用原子写入；既有 Session 目录清理同时移除它。TUI 手动压缩和恢复使用活动 Agent 的当前定义。核心没有反向导入 evaluation，也没有重建执行引擎。评测装配可接收预先准备的 Registry，自动化探针通过正式 worker 执行；现有 Live 容器入口仍限定六个基础业务工具，后续专项服务资产及容器部署属于题库建设，不能将本地服务验收冒充 Live 容器验收。

## 真实模型与成本

驱动为 [stage6_live.py](../../../../../evaluation/examples/stage6_live.py)，业务服务为 [stage6_tools.py](../../../../../evaluation/examples/stage6_tools.py)，只读复核/导出为 [stage6_review.py](../../../../../evaluation/examples/stage6_review.py)。两入口均使用 Anthropic 协议、deepseek-v4-flash、thinking=true、SDK 零自动重试、90 秒超时及 4096 输出上限；实际 thinking 参数沿用同一个产品适配规则。HOME、工作区与 Session 隔离，不启用主集辅助 Agent、用户记忆或自定义 Hook。

第一请求要求通过固定 CI 工具核对 BUILD-731 的状态、失败原因及重试条件；第二请求再次核对并返回相同四个 JSON 字段。预先连接的 MCP 与内置工具分别提供失败详情和状态。合同结果为 `status=FAILED`、`reason=TEST_TIMEOUT`、`retry_allowed=true`。两入口的两个最终回答均满足合同；两种业务工具各成功调用两次，业务目录无改动，曝光在第二个 Agent Run 中保留。

| 最终入口 | Provider 请求 | 工具请求 | 发现请求 | 实测 Token | 终止/清理 |
|---|---:|---:|---:|---:|---|
| 自动 worker | 6 | 8 | 4 | 18,841 | completed / passed |
| tmux 产品 App | 5 | 6 | 2 | 15,438 | completed / passed |

所有最终请求 usage 完整、无账本截断、无未关闭工具/时间阶段；实际 Provider close 各一次，MCP 连接任务全部退出，tmux 退出码为 0。最终两份源码/依赖快照完全相同且与交付源码字节一致，见各运行的 source-identity.json 与 [审计](audit.json)。请求输入产物证明第一轮仅暴露六个基础工具和发现入口，后续请求才包含已发现定义；相同任务不要求随机发现文本或调用次数逐字相同。

每次运行上限为 240 秒、150,000 Token、12 Provider 请求、16 工具请求，单请求预留上限 20,000 Token，资源关闭宽限 15 秒。首次四次尝试使用一个最多 750,000 Token 的临时 Campaign；复核修正 TUI 手动入口后，追加两次使用独立同上限 Campaign，不修改原预算或退还旧预留。两个 Campaign 的有限总上限合计 1,500,000 Token，实际仅开始六次，保留所有预留与原结果。它们是开发机制验收，未启动正式冻结实验。

[debug/](debug/) 保留最初两次，[prior-final/](prior-final/) 保留手动入口修正前的两次；没有覆盖原始运行。先前一份 tmux 中模型直接猜测两个未曝光工具，系统均拒绝执行，模型随后发现并完成，两个错误和重试成本保留。调试两份实测 36,460 Token，前轮两份 36,973，最终两份 34,279，六次累计 107,712 Token，无未知 usage；未核验金额，不估算费用。MCP 准备和关闭耗时在最终 result.json 单列；账本主端到端只覆盖应用/Agent/Provider/Session，不能将其当作含 MCP 服务生命周期的完整性能结果或用于机制收益比较。

## 质量与停止边界

最终全量 pytest 为 [873 passed](pytest.txt)，包含 11 个新增 Schema 合同测试，并补充既有 Skill fork、Hook、两种 Team 后端与 TUI 策略断言。[Ruff](lint.txt)、[格式](format.txt)、[严格 mypy](mypy-evaluation.txt)、[评测/曝光 C901](complexity-evaluation.txt)、[架构及增量门禁](boundaries.txt)、[锁文件](lock.txt)与启动检查通过。新曝光模块进入 Linux/Windows 增量门禁范围，只扩展目标列表，不增加历史诊断额度；本轮 Linux mypy 180→179、C901 21→20，新增诊断均为零。Windows 本地验证仍待第十二阶段，不由 WSL 结果代替。

版本由 0.1.30 递增至 0.1.31，pyproject、运行时、uv.lock 与安装 metadata [一致](version-identity.json)；uv.lock 仅改变项目自身版本。质量命令和退出码见 [quality-check.py](quality-check.py)、[quality-results.json](quality-results.json)。原始产物 `.tar.gz` 和屏幕 `.gz` 保留字节；解包到对应账本目录可按 SHA-256 核验请求、工具与 Session 内容。实际配置凭据经过含解压内容的字节扫描，未写入证据。

复跑需要两个全新运行目录，命令为 `.venv/bin/python evaluation/examples/stage6_live.py --root <外部目录>/runs/auto-final --kind auto` 和对应 `tmux-final --kind tmux`；这些命令会发起有界真实模型调用。只读复核为 `.venv/bin/python evaluation/examples/stage6_review.py --root <外部目录> --output <全新证据目录>`。本轮完成第六阶段后停止，不实施第七阶段题库、先导、冻结或正式实验，不读取或修改 docs/。按用户要求仅提交推送本阶段代码、测试、证据、任务进度和版本文件。
