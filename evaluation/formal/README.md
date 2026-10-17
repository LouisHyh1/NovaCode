# 正式配对执行与报告

2026-10-16 已按交接继续实施：147 次历史审计、11 次候选恢复及 10 个代表正负例的提取→归档→恢复→独立判题通过，见 [历史审计](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/history-audit-20261016/recovery.md) 和 [链路验证](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/evidence-chain-20261016/validation.md)。通用提取、可信类型化基线、保全和诊断已修复；24 题按固定元数据规则选出。新运行时部署和冻结由独立收据确认；真实模型调用仍待用户明确确认新身份及预算。v2 仍是 147/432，285 次未开始，其全部现存原工件、原评分及成本不删不覆。

仅在 WSL/Linux 运行。`run.py` 在外围顺序调用既有 `run_task()`，主循环、工具、权限与资源关闭使用冻结 wheel；不创建另一套 Agent。每次运行前校验完整冻结清单，Campaign 共用保守总预算账本，单任务并发度为 1。运行身份与调度脚本指纹写入独占创建的收据，原结果不覆盖。

v1 首块三次结果因公开格式缺陷停止比较，同时保留其 TUI 入口偏差；原评分、26,863 Token、源码快照和轨迹见 [v1审计](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/superseded-v1/)。用户确认后，v2 仅向四道定位题补充不含答案的通用 JSON 类型和关系编码，全部专项重新离线验收，Live 合同和原冻结资格复用。模型、任务业务、预算和两项消融不改；新版本 0.1.36，使用新 campaign、新原计划和新结果目录。

`compact_campaign.py` 提供分离的 select/deploy/freeze/verify 离线入口。`run.py` 只接受新清单及绑定其 campaign、manifest SHA-256 和总预算指纹的明确启动确认；旧队列被拒绝。计划、报告和状态根据对应清单推导规模，历史 432 行仍按旧规则核验。生成冻结清单和脚本化 tmux 均不启动真实模型；最终指纹、磁盘准入值和使用步骤见独立 v3 冻结报告，不使用旧首块审查文件代替新首块验收。

离线出口已取得新 [FROZEN_VALID](../frozen/campaign-v3/audit.json)，实际清单与 26 项身份/授权拒绝证据见 [本轮报告](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/compact-v3-20261016/offline-validation.md)。`.tmp/compact-v3-deployment-20261016/` 的初始基线和外部缓存中的 `runtime-canonical.tar` 现属于冻结资产，禁止当作普通临时文件清理；正式新队列尚未创建，不能借用 v2 身份或旧首块审查收据。

新方案以 [执行 v3 协议](../../openspec/changes/add-reproducible-agent-evaluation/evaluation-protocol.md) 为准：父池取 v2 原 48 题，选择方法 compact-v3-selection-v1、种子 20261016，12 Live＋12 冻结专项，每来源难度 4/6/2、专项类别 2/2/2/3/3，满足仓库和压力覆盖，选择器不读取结果。三配置各三次，216 行、每配置 72、每来源每配置 36；独立 campaign、协议/源码/运行时/基线/抽样/预算/统计指纹和存储，不混入旧 147 次。单次类别上限沿用开发先导，新总预留为 176,301,111 Token、7,317 Provider、13,187 工具、66,231 秒；准备、判题、归档另计。

现有 [中断审计](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/interruption-20261015/interruption-recovery.md) 是当时的原始报告，末尾旧路径选择已由本次用户决策替代，原文保留。修订前 [v2 协议快照](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/protocol-revision-20261016/before/openspec/changes/add-reproducible-agent-evaluation/evaluation-protocol.md) 与旧清单同哈希；当前协议路径修改后旧实时指纹核验预期失败，历史复核须显式验证同哈希快照及原资产，不能改旧清单或关闭校验。

前置失败保持未开始状态，原目录保留；恢复前置使用附加目录，但仍属于同一个尚未开始的原计划。已经开始的条目不再启动；缺结果、缺归档或中断账本会停止调度，必须先审计，不能删除目录后重跑。正式重跑须另建身份及协议，不能占用旧计划键，也不取最好结果。主集入口始终自动化，TUI 验收另列。

旧 `archive_run` 曾在提取失败、补丁缺失时释放业务工作区。新实现须从类型化初始树和完整补丁实际恢复，并验证内容/类型/执行位一致，才释放可再生临时副本；只验证现存 tar 成员不够。run 归档与按指纹去重的 baseline 归档共同构成恢复所需工件，`preservation.restore_run()` 会核验两者；`replay.py` 的旧报告恢复入口只恢复报告输入。缺补丁、复制/归档失败、损坏或不可重建时保留完整工作区与停止容器。历史 147 次全部现存原工件保留；v3 通过代表完整链路的空间低水位实测确定门槛，旧 6 GiB 常量不作为新保证。

优先离线恢复 11 次为 FL11/repeat1 三配置、FL14/repeat1 三配置、FL20/repeat1 三配置和 FL20/repeat2 full/eager-schema；第 147 次的补充补丁与保留工作区另行复核。恢复输出单独标明完整、局部、候选或证据不足，测试通过不能证明等于原补丁。全部 147 次都要审计影响范围，派生判题不覆盖原分数；无需新增模型调用，实际执行在全部文档修改完成后按任务计划进行。

报告分别展示 Live 与自建专项的计划、已开始、终止、独立判题、严格成功、Live resolved、缺失用量、工具与时间分布。金额按真实分项 usage 与执行时价格核算，未知不填零，协议中的容量场景不冒充账单。Token 不完整只报告已知下界；辅助配对成本只使用双方三次均完整的独立任务并披露缺口。两个主要比较为 full 减 no-compression、full 减 eager-schema 的严格成功率百分点差；三次先按任务取均值，任务级配对 percentile bootstrap 固定种子 20261012、10,000 次、95% 区间。v3 共 24 个独立任务、每来源 12 个；六题三次仍是六个独立任务，退化区间不支持确定性结论，历史报告使用其原分母。

事前压力子集来自冻结合同，失败和无触发任务仍纳入；实际 Layer1、Layer2、发现、故障覆盖另报。失败归因以原始终止、独立条件和工具轨迹为依据，人工抽查另存，不改原评分。全部结果只解释为所选题库下的条件证据，不称官方全榜成绩，接受零或负收益。

2026-10-17 当前执行身份更新为 0.1.37 / novacode-compact-20261017-v3-r1，清单位于 evaluation/frozen/campaign-v3-release-20261017；用户已明确要求提交推送后启动。先验收首块，再继续剩余新计划，旧清单和原结果仍保留；实时队列根为 .tmp/formal-compact-v3-20261017，不能使用旧 v2 或 0.1.36 身份启动。
