# 第八阶段 Live 开发题入库与验证报告

第八阶段 8.1–8.6 已完成，整体进度 48/71。12 道 Live 按 4 easy、6 medium、2 hard 入库，与重新核验后的 12 道开发专项共同形成 `DEV_ADMITTED`，共 24 道开发题。[最终入库状态](admission/admitted.json)、[Live 账本](admission/selection.jsonl) 与 [24 题交付审计](delivery-audit.json) 均可复核。版本为 0.1.33；实施止于第八阶段，不启动第九阶段先导、冻结或正式实验，验证后按本轮授权提交推送。

Docker Hub 匿名 Registry 曾返回 HTTP 429，选择器暂停整个选择过程，不把额度耗尽当作候选无效。用户在 WSL Docker CLI 登录后，认证请求恢复 HTTP 200，见 [限额记录](registry-rate-limit-initial.json) 与 [认证验证](registry-authentication.json)。恢复根 `stage8-authenticated` 复用 24 个已开始验收的资格记录，只恢复未开始测试的镜像准备，沿原规则继续。

## 完整池与事前规则

数据为固定 revision `b51a86422e10cfd403beb4773e5a2947953e36ec` 的完整 verified Parquet，24,696,977 字节，SHA-256 `080e36e46198bf9c177a6b077624d4028baf6ff04d661c332cc1fe1e5dfa50b2`，下载内容与官方 LFS 元数据一致。全部 500 行均纳入筛查；181 行可进入审查，316 行在事前声明的仓库范围之外，1 行合同审查排除，2 行为同一 ID 的冲突版本。`conan-io__conan-18153` 的两行均排除，不自行选择其中一行。原目录 12 候选的 ID/base_commit 均与固定数据对应，见 [身份核对](catalog-identity.json)。

本轮是预先限定 13 个 CPU Python 工程仓库的开发队列，不是整个 verified 的随机代表样本。种子 20261010，每仓库最多 2 道入库任务，完整池按 hard/easy/medium 依次核验，各层按 `sha256(str(seed) + ':' + instance_id)` 排序。同层替补、issue/fix 家族去重、最多 36 个不同候选的规则保存在 [事前规则](../../../../../evaluation/live/selection-rule.json)。标题仅作初分层，认知跨度必须在测试前审查，补充审查不能静默改变层或顺序，也不根据 NovaCode 成败选题。

公开请求通过重新构造白名单产生，仅保存原始 `problem_statement`，不复制未知字段。隐藏 row、参考补丁、测试补丁、hints 和 FAIL_TO_PASS/PASS_TO_PASS 名单交给外部准备与判题进程。Agent 运行复用产品主循环和现有专项运行器；不挂载外部验收资产、Docker socket 或本项目仓库。独立判题使用官方 grader commit `ad79b850f15e33992e96f03f6e97f05ddf9aa0be`，不改变官方完整测试命令。

## 最终 L01–L12 入库清单

每题均在四个全新离线容器中完成：原始代码目标失败且原有回归全部通过，参考补丁三次通过。第三次先安装独立 NovaCode/CPython 运行时并检查六个正式工具；目标解释器、pip freeze、HEAD 和工作树状态在安装前后保持一致。[逐题汇总](admission/qualification-summary.json) 保存耗时与峰值；[镜像身份](admission/image-identities.json) 保存 digest、image ID 与 rootfs。12 份 `Lxx-qualification.tar.gz` 保留原始资格文件，其 SHA-256 逐项核对通过。

| ID | 难度 | instance_id | 原始 / 参考三次耗时（秒） | 最大内存峰值（字节） |
|---|---|---|---|---:|
| L01 | easy | pylint-dev__pylint-10225 | 168.46 / 169.17 / 162.92 / 164.78 | 1220984832 |
| L02 | easy | joke2k__faker-2096 | 46.16 / 46.43 / 46.05 / 46.27 | 215977984 |
| L03 | easy | joke2k__faker-2093 | 46.39 / 46.90 / 48.35 / 47.88 | 211030016 |
| L04 | easy | python-babel__babel-1104 | 29.84 / 27.84 / 25.95 / 30.92 | 448151552 |
| L05 | medium | falconry__falcon-2426 | 88.26 / 80.85 / 79.09 / 84.24 | 1185099776 |
| L06 | medium | aws-cloudformation__cfn-lint-3749 | 58.05 / 58.12 / 57.92 / 58.29 | 386260992 |
| L07 | medium | pvlib__pvlib-python-2286 | 15.59 / 16.72 / 17.10 / 16.30 | 251998208 |
| L08 | medium | python-babel__babel-1120 | 54.63 / 44.81 / 46.21 / 46.84 | 450908160 |
| L09 | medium | jupyterlab__jupyter-ai-879 | 6.21 / 3.17 / 3.38 / 3.18 | 269750272 |
| L10 | medium | falconry__falcon-2248 | 73.13 / 66.81 / 66.54 / 70.16 | 1180393472 |
| L11 | hard | python-attrs__attrs-1321 | 24.47 / 23.05 / 24.52 / 22.49 | 154259456 |
| L12 | hard | pylint-dev__pylint-9990 | 168.84 / 170.18 / 170.76 / 172.17 | 1209155584 |

easy 层的局部比较检查、跨零随机数分布、locale 人名路由和 Locale modifier 均通过三次参考复验。原候选 GeoPandas 与 Kedro 在禁用外网的官方完整验收中存在原有回归失败或超时，因此按 easy 层固定顺序替补，没有调整测试或安装额外逐题依赖。原目录 Faker-2096 保留；其余初始候选与最终选择的差异见下表。

hard 层的 attrs-1321 涉及装饰完成时机、slot 替换类、继承 classmethod、ABC 与 Python 版本；pylint-9990 涉及 TYPE_CHECKING 导入的静态/运行期区别、延迟注解、消费者状态及局部作用域生命周期。二者无需真实训练、GPU 或权重下载。原目录 instructlab-2236 未证明 CPU/离线资格，未宣称其环境已实际失败；cfn-lint-3650 的多文件多数是 schema 更新，认知审查支持 medium，不能按文件数称 hard，见 [原困难候选审查](catalog-complexity-review.json)。Conan-16870 官方完整测试超过 600 秒，PDM-3419 原有离线回归失败，均保留实际失败后替补。

medium 层 6 道全部通过：Falcon 静态响应覆盖 WSGI/ASGI、日期、Range 与句柄关闭；cfn-lint 覆盖动态映射键与伪参数；PVlib 覆盖湿度/露点双向转换及标量/数组；Babel 覆盖 PO 位置写入、Unicode 文件名、换行与读回；Jupyter AI 覆盖异步片段累计、前缀/代码围栏后处理及最终帧一致性；Falcon Cookie 覆盖公开参数、Morsel 属性、序列化与版本兼容。Jupyter 官方参考测试使用本地 MockProvider，完整验收禁止外网且未调用付费模型。

实际按固定顺序核验 31 个不同候选，12 个通过、19 个排除，未超过事前 36 个候选上限；最终覆盖 8 个仓库，每仓库不超过 2 道，issue/fix 家族无冲突。标题分层、审查、仓库跳过与替补均在 [完整选择决定](admission/selection-decisions.json) 留证。没有依据 NovaCode 的代表运行成功与否选择或删除题目。

| 原候选 ID | instance_id | 本轮结果 |
|---|---|---|
| L01 | conan-io__conan-16776 | 最终配额已由固定顺序更前的有效候选填满，未启动资格测试 |
| L02 | geopandas__geopandas-3424 | 原有回归失败，按 easy 层替补 |
| L03 | joke2k__faker-2096 | 保留，最终为 L02 |
| L04 | kedro-org__kedro-4075 | 离线原有回归失败，按 easy 层替补 |
| L05 | bridgecrewio__checkov-6657 | 固定顺序更后，未启动资格测试 |
| L06 | falconry__falcon-2248 | 保留，最终为 L10 |
| L07 | jupyterlab__jupyter-ai-962 | 固定顺序更后，未启动资格测试 |
| L08 | pylint-dev__pylint-10350 | Pylint 已达到 2 道仓库上限，未启动资格测试 |
| L09 | pvlib__pvlib-python-2190 | 固定顺序更后，未启动资格测试 |
| L10 | pylint-dev__pylint-9785 | Pylint 已达到 2 道仓库上限，未启动资格测试 |
| L11 | instructlab__instructlab-2236 | 未证明 CPU/离线训练资格，在事前声明范围之外，未称实际环境失败 |
| L12 | aws-cloudformation__cfn-lint-3650 | 认知跨度支持 medium，不能按 schema 文件数称 hard；未进入本轮资格前缀 |


## 隔离、传输与失败留证

所有有效资格容器固定 2 CPU、4 GiB 内存与 Swap 总额、256 PID，官方完整命令上限 600 秒，网络为 none，宿主磁盘入库前至少保留 20 GiB，无独立容器磁盘配额。初次 `network_disabled=True` 导致 `/etc/hosts` 无正常 localhost，破坏本地 socket 测试；复现后统一改为 none，保留 loopback 且无外网接口/默认路由，见 [诊断](network-isolation-diagnosis.json) 和 [工厂检查](loopback-factory-check.json)。旧失败单列保留，修正后的环境记录具有新身份。

Docker 原生下载反复 EOF 后，备用准备通道从官方 Registry 校验原始 manifest、各 blob 大小/SHA-256，以 Docker load 导入，并核对 RepoDigests 与 rootfs diff_ids；只恢复首次测试之前的下载断流，不修改镜像内容。通道变化时复用此前首个已开始测试的资格目录。一次复用映射错误曾冗余启动 GeoPandas/Kedro 验收，Kedro 进程停止，相关记录及 replay-correction 留存，不从重复结果择优。恢复策略与具体过程见 [实现说明](../../../../../evaluation/live/README.md)。

共 62 个历史资格/准备记录已按目录归档，见 [清单](qualification-snapshot-before-registry-recovery.json) 和 [归档](qualification-snapshot-before-registry-recovery.tar.gz)。其中包含不同环境、准备重试和实际测试记录，不能将 62 当作独立任务数。10 个下载日志的临时签名 URL 查询串在交付副本中脱敏；清单记录原始与交付 SHA-256，原件继续保留在外部缓存，其余验收日志按原字节归档。

## 认证恢复与中断处理

认证后 cfn-lint-4051 与 3770 的原有回归失败，Conan-17123 的官方完整测试超过 600 秒，均保留实际结果后替补。Checkov-7119 的初始非空白工作树为未跟踪 CSV/console 产物，不是已证明测试被改写；它不符合本轮从首份资格脚本即执行的干净工作树规则，未清理或放宽条件后重判。Conan 其他镜像的已跟踪测试修改与这些未跟踪产物分开描述，以 initial-image-state.json 为准。

Jupyter 镜像 SSL 错误中的 `UNEXPECTED_EOF_WHILE_READING` 未被旧重试判定识别。以实际方括号错误格式复现失败，再修正通用准备重试判定；[修正前原始日志归档](ssl-retry-diagnosis.tar.gz)、[修正后](ssl-retry-after.txt) 与最初未复现的合成样例均保留。保护条件始终禁止复用该准备重试去重跑已开始的原始或参考测试。

用户中断后旧后台进程已停止，Jupyter 第二份准备目录没有原始/参考测试目录，也没有结束记录。保留旧文件，按授权补记 PreparationInterrupted；重新校验缓存 blob、完成官方镜像导入，再以第三份准备身份执行唯一一次原始验收和三次参考复验。后续控制器运行于独立 tmux 会话，日志写入持久缓存，完成后自动退出，见 [恢复记录](interruption-recovery.json) 与 [控制器退出码](selection-after-interruption-1.json)。

新增 13 个准备/资格记录与控制器、恢复日志归档于 [恢复历史](qualification-recovery-history.tar.gz)，[清单](qualification-recovery-history.json) 记录重复消费的旧记录与原始指纹。其计数不是独立任务数；最终选择仍由原固定种子、层和顺序决定。旧失败、原代表结果和账本均未覆盖。

## 真实 tmux 产品验收与专项判题修正

本阶段真实 tmux 运行共三次，使用既有装配、正常工具与权限，全部退出码 0，范围和清理检查通过，账本无未知 usage、尾部截断或未完成工具。两次 Live 代表运行都因预算停止，独立官方 resolved=false，strict_success=false；失败如实保留，不用于估计开发集成功率。第三次 S01 正常生成完整事实回答，最初评分器将解释中的配置 JSON 误认成第二份事实答案。修正为仅识别包含合同事实字段的字典，仍拒绝错误关系和多份完整答案。

S01 的原始 result.json 与账本不覆盖；修订合同对同一产物自动复评通过，新模型调用为 0，仅在 `grading-review-v2.json` 记录新合同下的严格成功。为保持当前专项合同一致，12 道专项都重新做正确/负例资格验证，并生成 [新入库账本](specialty-admission/selection.jsonl)。旧 judge/合同另存 [历史归档](specialty-before-parser-fix.tar.gz)；12 题公开输入、初始代码和环境按字节保持不变，见 [不变性审计](specialty-input-invariance.json)。

[真实运行汇总](representatives/review.json) 及三份原始 tar.gz 保留终态、源代码指纹、Provider/工具账本和 tmux 屏幕。总计 396,377 Token、28 次 Provider、34 次工具请求，预留总预算 800,000 Token，不补充调用或取最好结果。首次运行时 runtime archive 是 symlink，Docker cp 后解包失败且未调用模型；后续在调用方解析为真实文件，诊断记录单列。不同临时预算的 Live 运行不能充当三配置配对比较。

## 质量门禁与交付边界

最终完整 pytest、锁文件、Ruff/格式、增量严格 mypy、复杂度、架构和版本/帮助启动检查的命令与退出码见 [质量检查](quality-results.json)。复制到 12 个隐藏资产目录的同名 judge.py 使 mypy 默认模块映射冲突；采用 `--explicit-package-bases` 后，原始脚本与 12 份拷贝共 19 个源文件全部通过严格检查，不排除这些拷贝。此前的 919 项质量记录与初次映射错误均保留。

pyproject、运行时、uv.lock 和已安装元数据均为 0.1.33，见 [版本身份](version-identity.json)。真实模型使用的产品源码与独立运行时没有因宿主下载、恢复和错误标签修正而改动；宿主资格脚本与判题资产另有指纹。所有 48 个有效原始/参考资格容器均无 oom_kill，最高记录峰值 1,220,984,832 字节，耗时均在 600 秒之内。

[交付审计](delivery-audit.json) 重放 24 题账本、验证所有合同/资产与开发配额，并对交付文件及 tar.gz 成员检查本机 Provider API Key 和 Docker 登录凭据，未发现匹配。审计脚本可用 `.venv/bin/python openspec/changes/add-reproducible-agent-evaluation/evidence/stage-8/delivery-audit.py` 复核；输出按新身份保存，不覆盖原文件。[OpenSpec 严格校验](openspec-validation.txt) 与 Git 暂存白名单/空白检查另行记录。

本轮只完成开发题库入库与代表产品验收，不是完整官方榜单题库或成绩。先导、冻结、正式配对实验、统计分析及产品专项仍按后续阶段推进；不得将资格参考补丁成功、模拟工具测试或单题修订复评分混成正式模型成功率。
