# NovaCode 交接：离线审计与修复完成，新 v3 已冻结

交接日期：2026-10-16。当前 change：`add-reproducible-agent-evaluation`，进度 **69/78**。11.7–11.14 已完成，下一项 11.15；真实模型调用为零，未启动新正式队列、未提交或推送。此前 DOCUMENTS_READY 交接原文保存于 [handoff-before-offline.md](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/compact-v3-20261016/handoff-before-offline.md)。

## 当前出口与授权边界

已取得 HISTORY_AUDITED、EVIDENCE_CHAIN_VALID 和新 FROZEN_VALID。用户要求继续实施和恢复中断进程，已完成离线恢复、修复、回归、代表判题、抽样、运行时部署及新冻结；这不替代新正式调用的确认。**正式调用仍须用户明确确认新冻结身份及预算**；未得到该确认前不调用真实 Provider，不生成批准收据，不启动旧队列。

新 campaign：`novacode-compact-20261016-v3`，版本 `0.1.36`；manifest SHA-256：`9e2024690cb6c71c65516eaefe5eeb419a9af639762fa35ad989d62899ba6794`。24 个独立任务、216 行 PLANNED，每配置 72、每来源每配置 36；三配置各三重复，六顺序均衡、同题预算一致，Agent.run 迭代上限 25。新总预留：176,301,111 Token、7,317 Provider 调用、13,187 工具调用、66,231 秒，准备/判题/归档另计。新冻结的实际清单在 [campaign-v3](evaluation/frozen/campaign-v3/manifest.json)，不得修改原身份继续运行。

## 优先阅读

- [本轮离线报告](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/compact-v3-20261016/offline-validation.md)：修复、证据、已知限制、运行入口与失败保留。
- [tasks.md](openspec/changes/add-reproducible-agent-evaluation/tasks.md)、[冻结后的当前协议](openspec/changes/add-reproducible-agent-evaluation/evaluation-protocol.md)：只按下一未完成任务推进，不能以旧首块或本轮质量门禁勾选新正式任务、Stage 12。
- [freeze-validation.json](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/compact-v3-20261016/freeze-validation.json)：26 项身份/授权负例，未授权调度在创建队列和读取用户配置前拒绝。
- [历史审计](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/history-audit-20261016/history-audited.json) 和 [链路报告](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/evidence-chain-20261016/validation.md)。

## 历史与现场保全

原 v2 仍为 147/432，285 次未开始，全部现存原终态、归档、账本、资产和保留工作区不删不覆。最后复核原 90,183 个主机条目、82,745 个停止容器条目、147 个终态/归档配对及 95 个外部资产均不变。原容器 `novacode-eval-45b72375695f4bd0912308e1c2cfb0e5` 未启动或删除；未来先查实时进程，不可凭本文件直接启动。

11 次优先离线恢复都是候选，不追认为原补丁完整：8 个非空候选独立测试通过，3 个 FL20/repeat1 空候选未解决。权威索引为 history-audit-20261016/recovery-summary-v3.json，逐次审计为 history-summary-v2.json，前一版本与失败尝试保留。第 147 次另生成的 9 路径补丁通过官方测试，并实际恢复 76,097 条业务状态与保留工作区完全一致；原评分仍为 runner-error/cleanup-failed/ungradable。原未知 usage 35 请求、缺失主时间指标 147 次不回填。

`.tmp/formal-stage11-v1/`、`.tmp/formal-stage11-v2/`、各 history/evidence-chain/storage 失败现场不可清理；新增恢复、判题、临时文件均使用独立路径。旧协议和原运行时已通过同哈希快照核验，当前路径不再满足 v2 是预期停止条件，不能关闭检查。

## 冻结资产与环境

`.tmp/compact-v3-deployment-20261016/` 中 24 题基线及 baseline.tar.gz 已绑定新冻结，禁止作为临时缓存删除。运行时 `/home/louishyh/.cache/novacode-evaluation/compact-v3-runtime-20261016/runtime-canonical.tar` 和同目录 wheels 属于新冻结；旧 v2 缓存保持原字节。恢复归档须同时保留 run tar 与按指纹引用的 baseline tar，单有 run tar 不足。

实测增量峰值 2,367,832,064 字节，冻结最低可用空间 6,883,147,776 字节（约 6.41 GiB）；每次新任务前检查，不足则保留现场并停止。宿主虚拟内存耗尽曾对应两次 WSL 重启，未修改杀毒或全局配置；已保留中断记录和失败输出。

当前宿主 PowerShell，项目在 WSL UNC，Linux 命令经 `wsl -d Ubuntu-22.04 --cd /home/louishyh/projects/NovaCode -- ...`，使用 `.venv/bin/python`。Docker 在 `/snap/bin/docker`，运行时需要 PATH 包含 `/snap/bin`；Python 离线脚本使用 `PYTHONPATH=src:.`。`openspec` 在 Windows PowerShell 可用，WSL 未配置该 CLI。若读取 ledger，只输出聚合摘要，不输出巨大 initial_ignored 或凭证。

## 验证与下一步

完整质量脚本退出 0，1058 项测试通过，锁、Ruff/格式、严格类型/复杂度、架构、版本/启动门禁通过。24 题最终运行时离线部署、10 例正负机制链、第 147 次实际恢复、真实 tmux 中的脚本化 Provider 工具/回复/Session/退出通过；均不是新增模型性能证据或 Stage 12 正式专项验收。OpenSpec 严格校验与差异检查另有最新结果。

只读新冻结复核：`PYTHONPATH=src:. PATH=/snap/bin:/usr/bin:/bin:.venv/bin .venv/bin/python evaluation/formal/compact_campaign.py verify --output evaluation/frozen/campaign-v3`。拿到明确身份和预算确认后，才创建绑定 campaign、manifest 与预算指纹的批准收据，并在独立新根（建议 `.tmp/formal-compact-v3/`）执行首个三配置配对块；三次计入新 216 行，异常保留暂停，不为结果调参。不复用旧首块审查文件，不恢复旧 285 次。

当前分支 `eval/reproducible-agent-evaluation`，HEAD `3854954`。工作区包含先前未提交实现、0.1.36 和资产及本轮修复，不要还原、广泛暂存或归因于单轮。用户未要求提交；未来提交必须先按 AGENTS.md 递增补丁版本、核对所有来源，且版本变化会使当前冻结失效，需要重新打包与建立独立身份再讨论正式启动。项目 `docs/` 忽略，不读、不审查、不修改。用户未要求并行委派，本轮没有启动子 agent。
