# NovaCode 交接：0.1.37 发布，新 v3 已获启动授权

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
