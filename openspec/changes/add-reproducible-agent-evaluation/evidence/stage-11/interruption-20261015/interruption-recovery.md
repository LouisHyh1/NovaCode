# 2026-10-15 中断恢复与评测缺陷审计

用户要求恢复中断进程并继续第十一阶段。现场检查确认旧调度器和模型进程已退出，146 次原计划已有完整终态与归档；第 147 次 FL20/repeat2/no-compression 在请求中断时没有收尾结果，所属容器已停止，ExitCode=255、OOMKilled=false。具体宿主中断原因未知。恢复过程未发起新的模型调用，未重新执行已开始的原计划。

第 147 次的原始 orchestration 与 worker 账本保持字节不变，另以 recovery.jsonl 记录恢复收尾。终态为 runner-error、cleanup=failed、acceptance=ungradable、strict_success=false；已有 271,457 Token 的已知下界，最后一次请求用量未知。跨宿主中断后的单调时钟不连续，不编造该次完整端到端时间。恢复产物包括已校验归档、单独的恢复补丁和保留的工作区，见 [恢复记录](recovery-audit.json) 与 [产物核验](recovery-artifacts.json)。原停止容器保留，便于复核。

当前 v2 共记录 147/432 次原计划，285 次未开始；63 次严格成功，20 次无法判题。已知 Token 下界为 31,065,237，35 次请求用量未知，不视为零或完整账单；请求级错误包括 30 次 APIStatusError、4 次取消及本次 1 次没有结束事件的请求，具体 HTTP 状态或原因没有保存，不能据异常类名推断。部分报告从原始账本和已校验归档重建，见 [摘要](partial-report/summary.json)、[逐次记录](partial-report/runs.json) 和 [失败初步归因](partial-report/failure-audit.json)。未完成三个重复的任务不产生完整主要比较区间；这些数字不代表正式实验完成。

补丁提取的首个确定缺陷为参数聚合超限。FL20 的原始初始忽略清单有 75,763 项，排除 pathspec 约 5.65 MB，而当前 SC_ARG_MAX 为 2,097,152 字节。原 extract_patch 在实际工作区稳定抛出 OSError(errno=7)，不是 Agent 解题失败。最小复现仅需三份物理文件和 1,401 个路径规格；使用 NUL 分隔的标准输入传递规格可得到与小规模对照完全一致的补丁，见 [复现脚本](repro_argmax.py)。该脚本需在仓库根设置 PYTHONPATH=src:.:evaluation/specialty 运行。生产源码尚未修改，v2 冻结身份仍通过校验。

另一类缺陷是未跟踪的初始忽略文件没有进入补丁基线：修改现有覆盖率/缓存文件会被表示为新增文件，初始忽略符号链接也未被原清单记录。用原始补丁在固定镜像执行独立 git apply --check，FL07 的 .coverage/coverage.xml/htmlcov、FL08 的 Hypothesis 数据和 FL09 的初始符号链接均出现 already exists in working directory；这解释了另外 8 次无法判题，见 [独立应用检查](grader-application-reproduction.json)。修复不能只扩大参数上限或随意忽略文件变化，必须保持完整修改范围与初始状态合同。

归档逻辑另有缺陷：此前 archive_run 在没有生成 submission.diff 时仍释放业务工作区。当前有 12 次原始补丁缺失，其中本次第 147 次已保留工作区并使用独立恢复提取得到补丁；其余 11 次现有归档不足以可靠重建业务修改，不能宣称可重新判题，也不能根据残缺工具记录人工改判。原有评分及费用保留，缺口见 [产物缺失清单](artifact-gaps.json)。后续修复必须为提取失败保留原始业务状态。

本次恢复额外从固定原始镜像恢复 88 个被隐私字段名规则遮蔽的初始文件哈希，未使用 Agent 输出猜测基线。恢复补丁使用 NUL 标准输入规格处理，仅作为新增诊断产物；原始 ungradable 评分保持不变，未混入正式判题结果。

模型队列当前没有重新启动，正在等待用户选择协议路径：修复提取/基线/归档后新建 campaign 并重新执行 432 次；或维持 v2 冻结代码继续剩余 285 次并披露上述限制；或保留部分报告停止模型执行。现有协议明确“代码、预算或合同修订形成新campaign，不能混用冻结身份”。不能直接修改源码后把新旧执行混入同一冻结计划。第十一阶段尚未通过验收，提交推送仍待阶段完成后执行；tasks.md 进度保持 61/71，第十二阶段未开始。
