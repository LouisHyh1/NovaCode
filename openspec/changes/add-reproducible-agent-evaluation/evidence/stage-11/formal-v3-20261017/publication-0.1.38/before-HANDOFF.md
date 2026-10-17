# NovaCode 交接：v3 剩余评测已恢复，宿主内存监控自动暂停与恢复

2026-10-17 用户重新授权剩余评测，已从第 155 次恢复，原 154 次及此前部分报告保留。权威实时状态位于 F:\NovaCode-evaluation-cache\memory-supervision-20261017\status.json；Windows 监督进程独立于 WSL，内存不足停止派发、当前任务按原预算收尾，恢复稳定后继续；WSL 低于 1 GiB 时停止后续任务并生成新最终报告。策略及授权见 evidence/stage-11/formal-v3-20261017/memory-resume-20261017/。以下收口状态是上轮历史，不代表本轮已停止。

当前状态 **FINALIZED_PARTIAL / REPORT_REVIEWED**：154/216，严格成功 105/154；62 未开始。1 GiB 阈值已按用户要求实施，但两次准备阶段 WSL 重启先发生，未触及空间阈值；Windows 同期虚拟内存不足。后台队列已停止、运行授权已撤销，不自动恢复。最终报告见 openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/formal-v3-20261017/disk-policy-1g-20261017/最终评测报告.md。原全计划 11.2 与 Stage 12 未完成；第 11 阶段报告相关三项已完成，当前 73/78。以下为保留的发布和执行历史。

交接日期：2026-10-17。change `add-reproducible-agent-evaluation`，发布准备时进度 69/78，11.7–11.14 已完成；下一项为 11.15 首个三配置配对块。用户本轮明确指示：**“先进行提交并推送，然后启动评测”**。新 v3 及原预算启动授权已取得，不重复询问；先核对远端发布提交成功，再生成绑定身份的批准收据并调用模型。

## 当前身份与入口

版本 **0.1.37**，新 campaign **novacode-compact-20261017-v3-r1**，manifest SHA-256 **`0cf2d3fb6ba40d1f4f62b17d31cdb975c19eb5b2adb0ac89d0572922f703fa2f`**。发布清单：[campaign-v3-release-20261017](evaluation/frozen/campaign-v3-release-20261017/manifest.json)；[发布与启动报告](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/release-20261017/release-validation.md)。

24 独立任务、216 行、三配置各三重复，每配置 72、分来源各 36。原抽样、顺序、模型、工具、任务、单次预算、25 轮作用域和初始基线均不改；发布只更新版本、运行时和独立运行标识。总预留为 **176,301,111 Token、7,317 Provider、13,187 工具、66,231 秒**，准备/判题/归档另计。

正式新根为 `.tmp/formal-compact-v3-20261017/`，首块 FS17 的 full、eager-schema、no-compression。必须从实时结果、scheduler.lock、进程收据、归档和账本确认状态，不依据交接中的准备时状态盲目重启。先验收首块三次，再继续剩余计划；基础设施、身份、保全或 Provider 异常保留并暂停，不调参、不以重跑替换原终态。

## 证据和保全

[任务计划](openspec/changes/add-reproducible-agent-evaluation/tasks.md)、[协议](openspec/changes/add-reproducible-agent-evaluation/evaluation-protocol.md)、[离线恢复报告](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/compact-v3-20261016/offline-validation.md) 继续为权威输入。此前“等待明确确认”的条件已经由本轮用户指令满足；不据旧文字再次停下来问。正式模型执行前仍校验绑定新 manifest 与预算的批准收据。

原 v2 的 147/432、285 未开始及全部原终态/归档/账本/工作区保持原字节；原保留容器 `novacode-eval-45b72375695f4bd0912308e1c2cfb0e5` 不启动或删除。11 个恢复均为候选，8 非空通过独立测试、3 空且未解决；第 147 次 9 路径补丁另经完整恢复和官方测试，原 ungradable/cleanup-failed/runner-error 不改。未知 usage、时间及完整性缺口不回填，不与新主结果混算。

旧 0.1.36 v3 manifest `9e2024690cb6c71c65516eaefe5eeb419a9af639762fa35ad989d62899ba6794` 保留，不再作为当前执行身份。新版本的 24 题重新离线部署并实际生成基线，逐字相同后共享 `.tmp/compact-v3-deployment-20261016/` 已冻结初始对象；新收据在 `.tmp/compact-v3-deployment-20261017-v0.1.37/`。新运行时 `/home/louishyh/.cache/novacode-evaluation/compact-v3-runtime-20261017-v0.1.37/runtime.tar` 与所有旧缓存、失败现场均不得清理。最低可用空间 6,883,147,776 字节，约 6.41 GiB。

完整 1058 测试和质量门禁通过；实际部署、身份负例、原证据复核和密钥扫描见本次发布目录。上一轮 tmux 使用脚本化 Provider，是机制证据，不计 Stage 12 的真实模型专项。宿主曾发生虚拟内存耗尽和 WSL 重启，不能删除失败现场或据此回填旧时间。

## 工作区和操作

分支 `eval/reproducible-agent-evaluation`，本次提交前 HEAD 为 `3854954`，实际发布提交以 Git 与推送收据为准。使用明确文件清单暂存；docs/ 忽略，不读、不审查、不修改，不提交用户配置、记忆或 .tmp。用户要求提交已满足版本递增前提，所有版本来源为 0.1.37；后续若再次提交须再递增版本并重新冻结，不能在当前模型队列运行中随意修改已冻结源码、协议、版本或协调器。

宿主 PowerShell，WSL 入口 `wsl -d Ubuntu-22.04 --cd /home/louishyh/projects/NovaCode -- ...`。Linux Python 使用 `.venv/bin/python`、`PYTHONPATH=src:.`，PATH 包含 `/snap/bin` 以发现 Docker；uv 使用原清华镜像。OpenSpec CLI 在 Windows PowerShell。SSH 使用既有 Windows OpenSSH 与 ssh.github.com:443、HostKeyAlias=github.com，不关闭主机密钥校验。

只读冻结核验：`PYTHONPATH=src:. PATH=/snap/bin:/usr/bin:/bin:.venv/bin .venv/bin/python evaluation/formal/compact_campaign.py verify --output evaluation/frozen/campaign-v3-release-20261017`。运行状态入口：`PYTHONPATH=src:. .venv/bin/python evaluation/formal/status.py .tmp/formal-compact-v3-20261017 evaluation/frozen/campaign-v3-release-20261017/manifest.json`。读取 ledger 只输出聚合摘要，不输出巨大初始文件清单、凭证或完整配置。

2026-10-17 推送后首块已完成并复核，真实 3/216、FS17 三配置 strict=3/3、实测 Token 941,531、Provider 74 次，当前任务进度 70/78。仅一个独立任务，不外推机制收益。后续状态查看新队列账本及 `.tmp/v3-remaining-process-20261017.json`；新增运行证据和复选框尚未另行提交，已冻结实现与版本不改。

2026-10-17 用户因 C 盘仅余约 1.23 GiB 明确要求暂停：已完成并归档 83/216，下一未开始条目为第 84 行，剩余 133 行；监督器已退出，运行时启动许可已撤销，状态为 PAUSED_BY_USER，不自动恢复。日志末尾的启动确认校验失败是人工暂停闸门的预期结果，不是新的模型或冻结身份故障。暂停证据位于 evidence/stage-11/formal-v3-20261017/pause-20261017/；C 盘发现约 25 GiB 系统管理分页文件，Defender 私有提交约 15.93 GiB，WSL 约 6.06 GiB，需先处理宿主内存和 C 盘空间，未删除工件或更改系统设置。恢复时先验证空间与冻结身份、确认无活动进程，再用新的批准/启动收据从第 84 行接续；原初始监督脚本断言只有 3 份归档，不能直接重复启动，也不能删除已完成目录重跑。

2026-10-17 用户明确要求“空间已恢复，恢复评测进程”：C 盘约 34.8 GiB、Linux 工作盘通过原准入门槛，冻结身份及原 83 份结果/样本/账本/归档核验不变；已用新的 .tmp/approval-v3-resume-20261017-r1.json 从第 84 行恢复，原撤销许可和暂停记录不改。第 84 行 FS10/eager-schema 已严格通过并完成恢复校验，后续在 tmux novacode-v3-resume-20261017-r1 运行。状态仍查看 .tmp/v3-remaining-process-20261017.json，日志继续追加原 .tmp/v3-remaining-20261017.log。新运行级保护在每条启动前检查 C 盘至少 5 GiB；不足暂停后续任务，Linux 原冻结门槛及任务/模型/预算均不变。复核证据在 evidence/stage-11/formal-v3-20261017/resume-20261017-r1/。

2026-10-17 用户明确授权 WSL 最低剩余空间改为 1 GiB，并要求空间不足停止后续任务、按已完成样本生成最终结果。已保留 154 次原记录，从第 155 次以独立运行适配与授权收据恢复，原冻结文件及预算不改。允许按本轮新停止规则结案为 FINALIZED_PARTIAL，原报告仍标 PARTIAL 并保留未开始及原计划分母；全部 216 次记录时才标 FORMAL_COMPLETE。变更与证据见 evidence/stage-11/formal-v3-20261017/disk-policy-1g-20261017/execution-amendment.md。Stage 12 不包含在本轮主集收口授权中。

2026-10-17 最终主集交付已按用户要求收口为 FINALIZED_PARTIAL / REPORT_REVIEWED：154/216，严格成功 105/154，62 未开始；11.3/11.4/11.6 的故障记录、分来源统计和逐例审阅及原账本重建已完成，进度 73/78。11.2 的全 216 次 FORMAL_COMPLETE 条件不满足，保持未勾选；Stage 12 四项未执行。运行门槛 1 GiB 已实施，但恢复第 155 次的两次准备均遇 WSL 重启，Windows 同期 Event 2004 虚拟内存不足；没有新增模型调用，实际约 9 GiB 可用，未触发 1 GiB 门槛。两个中断现场保留，运行授权已撤销，不再自动恢复。最终交付见 evidence/stage-11/formal-v3-20261017/disk-policy-1g-20261017/最终评测报告.md。
