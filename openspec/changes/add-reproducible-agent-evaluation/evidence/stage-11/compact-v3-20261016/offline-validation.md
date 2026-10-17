# 历史保全、离线修复与精简 v3 冻结验收

日期：2026-10-16。状态：HISTORY_AUDITED → EVIDENCE_CHAIN_VALID → FROZEN_VALID。11.7–11.14 已完成，进度 69/78；真实模型调用为零，未创建新的正式执行队列，11.15 和后续正式报告、Stage 12 未完成。没有提交或推送。

## 新冻结身份

Campaign：`novacode-compact-20261016-v3`；版本：`0.1.36`；[manifest](../../../../../../evaluation/frozen/campaign-v3/manifest.json) SHA-256：`9e2024690cb6c71c65516eaefe5eeb419a9af639762fa35ad989d62899ba6794`。父 manifest 保持 `cf7191aeef652b2658456572fb737c17791649e65870ebba48812d1dd35de658`，原 v2 不续跑。

24 个独立任务 × 3 配置 × 3 重复，共 216 行且全部 PLANNED；每配置 72，每来源每配置 36，共 72 个配对块。每来源六种配置顺序各 6 块，同题三配置完全相同类别预算。迭代身份固定为每次 Agent.run 最多 25 轮；专项多轮合同分别使用该上限，不声称整个多轮任务总计 25 轮。

总预留上限：176,301,111 Token、7,317 Provider 调用、13,187 工具调用、66,231 秒；单请求 508,821 Token 预留、清理 55 秒。准备、判题和归档成本另计。这是安全预留，不是实际账单或预期耗时。

抽样固定为 compact-v3-selection-v1、种子 20261016，输入仅为父合同白名单元数据。选择、未选择、排序和输入指纹均在 [selection.json](selection.json)。Live：FL09, FL10, FL08, FL13, FL11, FL24, FL03, FL14, FL23, FL16, FL07, FL02；专项：FS11, FS06, FS07, FS24, FS19, FS10, FS16, FS04, FS22, FS13, FS03, FS17。每来源难度 4/6/2，专项类别 2/2/2/3/3；Live 覆盖 9 个仓库、每仓库至多 2 题，全部压力与家族约束通过。未参考成功率、消耗、补丁或失败分布；父池历史结果已经被查看的研究限制仍保留，不能宣称未见结果的外部留出集。

## 原历史保全与恢复

[最终保存核验](../history-audit-20261016/preservation-final.json) 对原始清单中的 90,183 个主机条目、82,745 个停止容器条目、147 份终态/归档配对重新核验，原字节、文件类型、权限和链接目标均未变。新增派生文件仅允许出现在明确列出的新证据目录。原保留容器未启动、删除或改写，旧模型队列未启动；[95 个原外部执行资产](original-external-verified.json) 全部保持原哈希。

11 次优先恢复全部单独输出为候选：8 个非空候选通过官方独立测试，3 个 FL20/repeat1 轨迹没有业务修改调用，得到空候选且未解决。历史轨迹不含原最终完整树指纹，故候选通过测试仍不能追认为原补丁或完整恢复。涉及脱敏的 FL14 上下文，仅从固定原初始源文件唯一匹配处恢复共同旧/新文字，恢复位置与哈希单列，没有使用参考答案补齐。各轮失败和修订前收据保留，权威入口为 [recovery-summary-v3.json](../history-audit-20261016/recovery-summary-v3.json)。

[逐次历史审计](../history-audit-20261016/history-summary-v2.json) 保留 147 次原评分，其中原 strict=63、ungradable=20；不以派生结果改写。可用 Live 补丁中 55 次在原固定初始环境可应用、8 次存在初始文件伪新增冲突、12 次原补丁缺失；72 次专项原判题工件完成哈希关联核验，未把它们宣传为全部重新判题。丢弃的最终业务树无法普遍证明完整，未知 usage 的 35 个请求和原 147 次缺少的主时间指标保持未知。

第 147 次由固定原初始树和保留工作区另生成 9 路径补丁，SHA-256 `4842d0b1d159ccc2b78d6c97ed3aa723f7ae99e253f9e1752c9bd01dd991b1aa`，官方独立测试通过；[实际恢复比对](../evidence-chain-20261016/interrupted-state-verified.json) 的 76,097 个业务条目内容、类型及执行位与保留工作区一致。旧补充补丁中的初始链接伪新增另存失败证据；原 runner-error、cleanup-failed、ungradable 及缺失成本不变。

## 修复、链路和验证

提取以 NUL 标准输入传送路径，消除 E2BIG；可信初始基线保存路径、类型、内容或链接目标与必要模式，使用固定 base、隔离索引和对象存储，禁用不可信 Git 配置、Hook 和属性转换。初始忽略文件、链接、二进制、已提交修改均可表达真实增删改，不再用只有哈希的旧基线掩盖变化。大型基线打包为 Git pack，避免数万松散对象复制；树和清单指纹保持不变。

归档实际重建并逐项比对后才释放可再生工作区和所属停止容器；缺补丁、复制失败、损坏、不可恢复或中断时保留现场。恢复需要 run tar 和按指纹引用的 baseline tar 两者，旧 replay 入口只恢复报告输入。有界异常链和 HTTP 状态、错误码与可用错误摘要经脱敏写账本，不记录完整认证头或响应体，不把未知 usage 回填为零。

FL20、FL07、FL08、FL09、FS09 共 10 例提取→归档→恢复→独立应用→外部判题，5 正例通过、5 负例失败；详见 [链路报告](../evidence-chain-20261016/validation.md)。FL09 首次夹具错误、FS09 首次调用方输入缺失及两个宿主中断的失败现场均保留，未改隐藏判题器。该验收是机制证据，没有新模型效果结论。

24 个选中任务全部使用最终运行时在无网络容器离线部署；六类实际工具 smoke、目标依赖/Python/源码前后不变、逐题类型化初始基线与 25 轮装配检查通过。小型收据见 [部署证据](deployment/deployment.json)；完整基线保留在 `.tmp/compact-v3-deployment-20261016/`，现为冻结资产，不可清理。新运行时位于 `/home/louishyh/.cache/novacode-evaluation/compact-v3-runtime-20261016/runtime-canonical.tar`，旧 v2 运行时未动。

FL20 全链路按文件系统剩余空间每 50 ms 采样，实测增量峰值 2,367,832,064 字节。冻结门槛为 `max(2×实测峰值, 保守逻辑工作量)+2 GiB = 6,883,147,776` 字节，约 6.41 GiB。采样含宿主并发写入的可能影响，按保守估计使用；不能据此保证所有失败现场无限保存。原始曲线见 [storage-measurement.json](../evidence-chain-20261016/storage-measurement.json)。

完整 `scripts/validate_change.py` 退出码 0，[quality.log](quality.log) 记录 1058 项测试通过、uv lock、Ruff/格式、严格类型检查、复杂度、架构和版本/启动通过；历史 mypy 基线 180→179、新诊断 0，C901 21→20、新诊断 0。先前完整回归曾因极短超时在准备落盘阶段耗尽而出现两个失败，原日志保留；测试改为等待实际流/子进程启动后触发超时，仍验证真实取消和进程回收，生产预算未放宽。

脚本化 Provider 在真实 tmux 中完成正式 NovaCodeApp→Agent→read_file→回复→Session→退出，Provider 只关闭一次，见 [tmux 验收](../evidence-chain-20261016/tmux/validation.json)。该测试不计 Stage 12 的真实模型验收。宿主内存耗尽事件和 WSL 重启事实见 [host-interruption.json](../evidence-chain-20261016/host-interruption.json)，未修改全局杀毒或系统内存配置。

[冻结负例](freeze-validation.json) 共 26 项身份/授权变更全部拒绝：源码、协议、协调器、抽样、预算、初始树/清单/归档、迭代、计划、工具、运行时、前置门禁、存储与授权绑定。另实际调用未授权调度入口，确认在创建队列和读取操作者模型配置之前拒绝，未调用模型、未生成批准文件。脚本和日志分别保存在 [脚本快照](identity-verification-script.txt) 和 [执行日志](identity-verification.log)。

## 复核与下一步

离线核验可用 `PYTHONPATH=src:. PATH=/snap/bin:/usr/bin:/bin:.venv/bin .venv/bin/python evaluation/formal/compact_campaign.py verify --output evaluation/frozen/campaign-v3`；此命令只读，不启动模型。修改被冻结源码、协议、依赖、协调器或版本后须重新建立独立身份，不能覆写此清单继续实验。

下一项仅为 11.15。依据当前协议“正式调用仍须用户明确确认新冻结身份及预算”，先向用户展示上述 campaign、manifest SHA 和预算，得到明确确认后才创建绑定身份的批准收据并执行首个三配置配对块。新根建议 `.tmp/formal-compact-v3/`；不能启动旧队列或复用旧首块审查文件。首块三次计入新 216 行；异常保留并暂停，不能调参或换题。其余正式运行与报告按后续任务门槛推进，离线冻结不是 FORMAL_COMPLETE。
