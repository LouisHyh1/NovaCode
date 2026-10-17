# 0.1.37 发布与正式评测启动准备

2026-10-17 用户明确要求“先进行提交并推送，然后启动评测”。该指示承接已展示的 v3 24 题/216 行及预算；发布所需版本递增和重新冻结属于已授权步骤，不重复索取启动确认。先完成本次提交并推送，在核对远端提交身份之后才生成绑定新清单的批准收据并调用模型；本报告生成时真实模型调用为零。

## 独立发布身份

版本 0.1.37，新 campaign `novacode-compact-20261017-v3-r1`，清单 SHA-256 `0cf2d3fb6ba40d1f4f62b17d31cdb975c19eb5b2adb0ac89d0572922f703fa2f`，见 [发布清单](../../../../../../evaluation/frozen/campaign-v3-release-20261017/manifest.json)。旧 v2 和未调用模型的 0.1.36 v3 清单保持原字节，新运行标识不复用旧结果或首块审查收据。

[release-validation.json](release-validation.json) 证明新旧 selection、policy、budgets、corpus、tools、input_guards、baselines 与磁盘准入完全一致；216 行只更换 campaign 和 run_id，顺序、配对、预算及任务指纹不变。新源码身份相对上次仅项目版本三来源变化，协调器仅新增独立 campaign 参数和逐字一致时的基线归档复用，不改变 Agent 解题策略。版本、运行时、锁文件和安装元数据均为 0.1.37，uv.lock 除项目版本外与 HEAD 相同，保留原清华镜像。

新 runtime 位于 `/home/louishyh/.cache/novacode-evaluation/compact-v3-runtime-20261017-v0.1.37/runtime.tar`；24 题均在原固定镜像中无网络部署，工具 smoke、目标依赖/Python/源码不变、25 轮装配与实际新建的初始基线核验通过。清单逐字一致后复用 `.tmp/compact-v3-deployment-20261016/` 已冻结基线对象及归档，新部署收据在 `.tmp/compact-v3-deployment-20261017-v0.1.37/`；这些目录及旧运行时不能清理。

## 验证与发布边界

完整质量门禁通过：1058 项测试、Ruff/格式、严格类型、复杂度、架构、版本/启动及锁检查；见 [quality.log](quality.log)。新清单完整核验以及 26 项身份/授权负例见 [freeze-validation.json](freeze-validation.json)。根 AGENTS.md 的提交前版本递增已执行，暂存采用明确文件清单，不包含项目 docs/、用户配置、真实记忆和 .tmp 原始工作区。

[保存复核](preservation-prepublish.json) 证明 147 组旧终态与归档、原工作区和停止容器不变；95 个原外部执行资产再次逐哈希相同。提交 21 份原判题输出、脚本及候选 diff 时发现原空白会触发 Git 检查，未清洗原证据；仅在 history-audit-20261016/.gitattributes 对这 21 个确切路径固定原行尾及空白，项目实现和文档仍执行原检查，见 [逐字保存说明](raw-artifact-byte-preservation.json)。

[发布扫描](publication-audit.json) 检查当前 Provider 密钥在待提交文件及压缩成员中均未出现；大体积恢复快照仍留在本地，不上传。SSH 22 关闭后，使用已有 Windows OpenSSH、HostKeyAlias=github.com 与 ssh.github.com:443；远端分支在发布前为 3854954，与本地原 HEAD 一致。公开远端模型及价格仅做只读核验，见 [provider-preflight.md](provider-preflight.md)，未增加模型调用。

## 推送后执行

独立新队列根为 `.tmp/formal-compact-v3-20261017/`，计划首块为 FS17 的 full、eager-schema、no-compression；先运行并审核三次的实际策略、预算、终止、完整补丁、真实归档恢复和独立判题。三次计入 216 行，普通任务失败或预算终止按原计划记分，不重跑择优；基础设施、保全、身份或 Provider 异常保留后暂停。

批准收据在推送成功后绑定新 campaign、manifest SHA、总预算指纹及实际发布提交。总预留仍为 176,301,111 Token、7,317 Provider、13,187 工具、66,231 秒，准备/判题/归档另计。磁盘门槛仍为 6,883,147,776 字节；不足时保留全部现场并停止新任务。首块审核通过后才生成新根的审查收据并继续剩余计划，不恢复旧 285 次或混算历史。

此报告只是本次发布与启动准备，不将未开始的正式执行、结果报告或 Stage 12 标为完成。实际推送与运行状态以后续 `.tmp` 中的推送收据、进程收据、账本和结果为准；本次提交中的文档不预先宣称未来模型结果。
