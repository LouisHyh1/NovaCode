# 第二阶段验收报告

2026-10-09 完成 2.1–2.4，整体进度 9/71。此次交付为版本化数据合同、资产校验、配额/家族审计和选择账本；第三阶段及其后任务未实施。两个样例仅用于合同机制验证，没有新增真实已入库题目，没有发起真实模型调用，也没有取得 CODE_READY、DEV_ADMITTED、PILOT_COMPLETE 或 FROZEN_VALID。

## 实现与验收对应

| 任务 | 实现 | 本轮验证 |
|---|---|---|
| 2.1 | `src/novacode/evaluation/contracts.py` 的 EvaluationTask、EvaluationRun、EvaluationCampaign；schema_version=1、规范 JSON 与 SHA-256、终止/验收/范围/清理独立、严格成功派生、原计划与补跑身份分开 | 单请求与三个固定请求组成两个任务、两个 Evaluation Run、四个 Agent Run；JSON 往返一致；跳过请求、未知字段/版本及重复原计划键拒绝；超时且 Live resolved=true 仍不严格成功 |
| 2.2 | `assets.py` 的路径、真实文件 SHA-256、独立资产根、公开输入白名单、单次 Live rollout、预算类别和 Campaign 引用检查 | 缺失资产、伪指纹、未知预算类别、越界路径、符号链接逃逸、公开/隐藏根重合、答案字段混入公开 JSON 及运行身份不一致均拒绝；入库时重新验证公开资产 |
| 2.3 | `corpus.py` 的重复 ID/候选/内容、家族/issue/fix/defect 键隔离、分来源/难度/专项类别配额 | 同 issue、改名缺陷、跨集家族、重复候选/内容及大小写/尾斜线变体拒绝；独立业务事实共享通用构建器允许；72 条机制 fixture 验证 12/12/24/24 来源目标及所有难度/类别配额 |
| 2.4 | 单写入者 SelectionLedger；候选→选择→入库→冻结，排除/替换保留历史，Qualification 绑定合同及外部证据 | 无资格证据不能入库；Live 原始目标失败、原有行为通过、参考连续三次成功为必需；专项要求正确/负例核验；重启重放一致，非法转换/合同变化/截断尾行拒绝；Agent 成败不能作为替换原因，替代题保持配额，冻结后不能替换 |

源码未增加另一套 Agent 引擎，产品核心没有导入 evaluation。新增反向依赖架构测试；评测包加入 `scripts/check_boundaries.py` 的严格 mypy/C901 门禁范围。质量基线只新增检查路径，没有新增或放宽任何历史诊断。

## 质量验证

全部命令和退出码保存在相应日志：完整 pytest [778 passed](pytest.txt)；其中新增合同测试 52 个、架构测试 1 个；[Ruff lint](lint.txt)、[格式](format.txt)、[评测包严格 mypy](mypy-evaluation.txt)、[新增模块与样例 C901](complexity-evaluation.txt) 均通过。[增量边界门禁](boundaries.txt) 为 mypy 180/180、C901 21/21、新增诊断均为 0；[锁文件](lock.txt)、[版本启动](startup-version.txt)、[帮助启动](startup-help.txt)、[OpenSpec 严格校验](openspec.txt) 均通过。

项目从 0.1.26 递增到 0.1.27，pyproject、运行时、uv.lock 和已安装 metadata 一致，见 [version-identity.json](version-identity.json)。锁文件由 uv 生成，保留原有包索引和依赖版本，Git 差异仅 NovaCode 自身版本变化。tmux 验证先于版本递增进行，其 0.1.26 横幅是当时实际版本；递增后重新确认版本和启动，不改写历史轨迹。

## 样例与 tmux

可复跑合同样例位于 `evaluation/examples/`，运行 `uv run --locked python evaluation/examples/validate_contracts.py`。样例结果见 [sample-validation.json](sample-validation.json)：两个样例、两个单配置/单重复的计划评测运行、四个计划 Agent Run、真实已入库数量为零，配额缺口明确展示。样例没有实际数值模型预算，不可当作可执行 campaign。

tmux 在新目录 `/tmp/novacode-stage2-tmux` 启动正式 NovaCodeApp、Agent、默认六工具、正常 DEFAULT 权限引擎和 SessionService，使用 `evaluation/examples/tmux_contract_probe.py` 中明确标注的脚本化 Provider；没有连接模型端点、读取用户真实记忆或加载项目 docs。输入的真实交互请求为“请读取 contract-summary.json，核对单请求和多轮脚本的任务、评测运行、Agent Run 及入库计数，并说明结果。”正式 read_file 工具读取实际校验器生成的计数，TUI 展示最终回复，用户输入、工具结果及回复写入 Session；Ctrl+C 退出后 pane 退出码为 0、Provider 关闭一次、测试 tmux Session 已清理。

证据为 [tmux-pane.txt](tmux-pane.txt)、[tmux-events.jsonl](tmux-events.jsonl)、[tmux-session.jsonl](tmux-session.jsonl) 和 [tmux-verification.json](tmux-verification.json)。这是脚本化 Provider 下的产品链路机制验证，不是自主模型任务成功率，不替代第四阶段真实模型与自动/TUI 一致性验收。

## 验证边界

本阶段校验资产路径、可读取性、字节指纹、合同完整性及资格声明的证据绑定；没有实现容器挂载隔离、初始 Git 状态重建、运行预算或独立判题。样例初始 fixture 含演示 base_commit，不能冒充已核验仓库。Qualification 的声明真实性要在各题建设/入库阶段由实际原始与参考验收产生证据，本阶段测试中的资格记录和 72 题 fixture 均为机制负例/对照，不登记真实题库。

家族与同源键依赖 curator 审查，校验器不自动推断未声明的任意语义克隆；任务冻结也不表示完整 campaign 已冻结。选择原因白名单拒绝显式 Agent 成败原因，但仍需审查说明正文，防止将事后筛选伪装成合法事前原因。账本只支持单写入者，截断尾行会明确报错，不能自动丢弃原记录重试。

本轮工作区初始干净，仅提交上述阶段实现、样例、测试、门禁扩展、进度及版本文件；未读取、审查或修改项目 docs/，未修改长期记忆。最终交付指纹见 [delivery-fingerprints.sha256](delivery-fingerprints.sha256)。
