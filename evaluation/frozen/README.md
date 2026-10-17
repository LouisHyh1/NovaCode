# 冻结题库与执行身份

当前产品版本 **0.1.39** 已完成独立的 [Stage 12专项](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-12/product-validation.md) 与完整质量门禁；整体78/78。本轮包含Session恢复菜单焦点修复，不把0.1.39作为既有主集受测版本，也不恢复旧模型计划。

当前完整执行结果为 **novacode-compact-20261017-v3-r1，216/216 FORMAL_COMPLETE**，受测 0.1.37；0.1.38 仅发布结果和更新文档，未改变原冻结清单。新工作树版本及状态文档与旧锚点不同，不能直接用原 manifest 恢复模型执行。原执行文件快照、全部结果及方法偏离见 [最终报告](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/formal-v3-20261017/publication-0.1.38/最终评测报告.md)。以下 PLANNED、零调用与等待确认均标识原冻结/准备时点，历史 v2 仍为 147/432。

## 历史方法与阶段记录

本目录保留第十阶段的离线入库、冻结及计划生成历史：24 道固定版本 SWE-bench-Live 任务覆盖 13 个仓库，另有 24 道独立专项；两来源分别满足 8/12/4 难度，专项类别 4/4/4/6/6。原清单有 48 个冻结合同、432 个初始 PLANNED 行，当时冻结步骤真实模型调用为零；见 [冻结验收报告](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-10/freeze-validation.md)。这不表示后续执行至今仍为零：v2 已有 147 次终态、285 次未开始，当前停止旧模型队列并保留全部现存原始工件。

2026-10-16 文档修订完成后，用户指示继续实施；已完成 147 次历史审计、11 次候选恢复、通用证据链修复及代表正负例验收，取得 HISTORY_AUDITED 和 EVIDENCE_CHAIN_VALID，并按 [v3 协议](../../openspec/changes/add-reproducible-agent-evaluation/evaluation-protocol.md) 选出 12 Live＋12 专项。新运行时、类型化初始基线与 216 行冻结由独立 v3 清单及审计确认；真实模型实验尚未启动，新冻结成功也不自动授权调用，须用户明确确认身份及预算。

v3 保留本目录全部 48 题父资产，按 compact-v3-selection-v1 和种子 20261016 选取 12 Live＋12 冻结专项；每来源 4/6/2，专项 2/2/2/3/3，满足压力与仓库约束，开发 L/S 题不进入正式子集。新计划 24×3×3=216 行，每配置 72、每来源每配置 36，独立执行身份和预算账本；父池已查看的历史限制保留。类别单次上限不改，总预算依新配额重算，具体见协议。冻结、报告与状态代码已按清单推导规模；v3 的实际出口以 `campaign-v3/audit.json` 为准，未取得该审计不得宣称新冻结通过。

2026-10-16 新 [audit.json](campaign-v3/audit.json) 已为 FROZEN_VALID：24 题最终运行时离线部署通过，216 行全部仍为 PLANNED，正式模型调用为零。26 项身份与授权负例及准入门槛见 [离线报告](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/compact-v3-20261016/offline-validation.md)。这一步只冻结新身份，不启动新模型队列。

`live-selection-rule.json` 在首次资格验收前固定完整 500 行题池、种子、仓库范围与上限、开发问题键、已留证的无效候选和认知审查。后续同层审查与替补保留资格证据，不能依据 NovaCode 成败选题。Live 必须原始目标失败、原有回归通过、参考连续三次通过；最终安装的 Agent 运行时另行逐镜像核验，不改变项目解释器与依赖。

专项的 `definitions.py` 和 `specialty-assets/external/` 只由外部建设与判题进程读取。Agent 仅收到固定公开脚本、初始文件和公开工具业务记录；隐藏验收与参考修复不进入产品 wheel 或任务容器。`judge.py` 复用现有事实和行为判题，补测题根据显式被测符号核验正确实现与全部缺陷，拒绝收集错误、跳过、无条件失败和范围越界。

`freeze.py build` 校验完整题库、部署证据、源码/依赖/官方判题/镜像/参数/权限/Hook/记忆/工具/预算和统计身份，生成 `campaign/manifest.json`、标准 Campaign 合同与 432 个 PLANNED 原计划。每题三配置各重复三次；每来源分别均衡六种配置顺序，单任务并发度为 1。没有真实 Provider 执行命令，冻结步骤模型调用为零。

原 `freeze.py build/verify` 和 `audit.py` 保持历史 48 题规则；新流程由 `evaluation/formal/compact_campaign.py` 分离选择、部署、冻结和核验，不修改旧清单或原资产。旧协议原字节与修订前文档保存在 [快照清单](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/protocol-revision-20261016/preservation.json)；旧实时核验因现行协议及源码变化而失败是预期停止条件。历史审计显式映射到同哈希协议快照并核对原执行资产，不绕过校验恢复旧队列。新清单绑定修复后源码/协调器/运行时、类型化基线、抽样证据、216 行规模、预算和分析身份。

正式入口仍是已有 `run_task()`，冻结任务在其前置阶段核验清单锚点、原计划、全部源码和资产、实际参数与剩余总预算。清单、代码、SDK、验收、工具或预算变化会阻止模型调用；改变版本必须创建新身份。清单绑定本机外部运行时与官方判题文件，迁移机器需要重新核验，不能保留失效路径或指纹。源码身份使用发布前 base commit 与实际执行文件树，提交生成的新 commit 不改写冻结身份。

Windows Registry 通道只用于宿主镜像准备：沿用 Docker 登录，网络路由在每个准备身份中记录为既有本地代理或 Windows 直连，认证内容不入库；大文件超时前缀交给既有 `.part/Range` 恢复，完整大小、SHA-256、导入 digest 与 rootfs 均须通过。新生成的传输 tar 在核验导入后释放，已留证的资格与失败不删改。

原生和 Windows curl 均可用 `NOVACODE_REGISTRY_TEMP` 指定临时目录。当前准备身份将其设为 `/home/louishyh/.cache/novacode-evaluation/registry-temp`，实际存储在 `F:\WSL\Ubuntu-22.04\ext4.vhdx` 内；Windows curl 仅负责网络，二进制 stdout 由 WSL 写入该目录，响应头通过同目录的 UNC 路径传递。此前 F 宿主目录通道与校验记录保留；不要恢复为 C 盘默认 Windows Temp。下载超时或中断会停止所属 Linux 准备进程组。存储诊断、实际下载核验和缓存迁移见 [storage-diagnosis.md](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-10/storage-diagnosis.md)。恢复脚本只启动资格控制器，不执行真实模型实验或自动提交。

`tmux_probe.py` 使用脚本化 Provider 检查正式 TUI、工具调用、回复、Session 与退出，是无模型的产品链机制证据。文档轮未执行探针；后续离线实施已完成新一轮真实 tmux 产品链验收，证据另存于 stage-11/evidence-chain-20261016，不能据此宣称新增真实模型效果或完成 Stage 12 的真实模型专项。远端权重无法不可变固定、推理子计数及实际账单未知、先导无 Layer2 触发的限制继续保留。

2026-10-17 当前执行身份更新为 0.1.37 / novacode-compact-20261017-v3-r1，清单位于 evaluation/frozen/campaign-v3-release-20261017；用户已明确要求提交推送后启动。先验收首块，再继续剩余新计划，旧清单和原结果仍保留；实时队列根为 .tmp/formal-compact-v3-20261017，不能使用旧 v2 或 0.1.36 身份启动。
