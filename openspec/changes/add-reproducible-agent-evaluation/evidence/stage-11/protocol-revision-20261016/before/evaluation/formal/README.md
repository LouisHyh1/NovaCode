# 正式配对执行与报告

仅在 WSL/Linux 运行。`run.py` 在外围顺序调用既有 `run_task()`，主循环、工具、权限与资源关闭使用冻结 wheel；不创建另一套 Agent。每次运行前校验完整冻结清单，Campaign 共用保守总预算账本，单任务并发度为 1。运行身份与调度脚本指纹写入独占创建的收据，原结果不覆盖。

v1 首块三次结果因公开格式缺陷停止比较，同时保留其 TUI 入口偏差；原评分、26,863 Token、源码快照和轨迹见 [v1审计](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/superseded-v1/)。用户确认后，v2 仅向四道定位题补充不含答案的通用 JSON 类型和关系编码，全部专项重新离线验收，Live 合同和原冻结资格复用。模型、任务业务、预算和两项消融不改；新版本 0.1.36，使用新 campaign、新原计划和新结果目录。

```bash
export PATH="$HOME/.local/bin:/snap/bin:$PATH"
.venv/bin/python evaluation/frozen/specialty.py build --assets evaluation/frozen/specialty-assets-v2 --image <固定镜像digest>
.venv/bin/python evaluation/frozen/specialty.py validate --assets evaluation/frozen/specialty-assets-v2 --output <新的入库目录>
# 运行时打包、全镜像部署后，由refreeze.py以新目录创建v2，不覆盖已有清单。
.venv/bin/python evaluation/formal/run.py --root .tmp/formal-stage11-v2 --limit 3
# 审计首块后保存first-block-reviewed.json，再按批次继续。
.venv/bin/python evaluation/formal/run.py --root .tmp/formal-stage11-v2 --limit 24
.venv/bin/python evaluation/formal/report.py --root .tmp/formal-stage11-v2 --output <新的报告目录>
```

前置失败保持未开始状态，原目录保留；恢复前置使用附加目录，但仍属于同一个尚未开始的原计划。已经开始的条目不再启动；缺结果、缺归档或中断账本会停止调度，必须先审计，不能删除目录后重跑。正式重跑须另建身份及协议，不能占用旧计划键，也不取最好结果。主集入口始终自动化，TUI 验收另列。

每次判题结束后归档原始账本、请求输入、最终文本、完整补丁、判题输出和 `.novacode` 状态；逐成员核验 SHA-256 后只释放可由固定镜像/base 与补丁重建的业务工作树。归档收据保留每个成员与压缩包指纹，报告重建时同时核对归档与保留的工作副本。存储不足 6 GiB 时停止新运行；不会通过改预算或删原结果继续。

报告分别展示 Live 与自建专项的计划、已开始、终止、独立判题、严格成功、Live resolved、缺失用量、工具与时间分布。金额未知，不从先导旧价格推算当前账单。Token 不完整只报告已知下界；辅助配对成本只使用双方三次均完整的独立任务并披露缺口。两个主要比较为 full 减 no-compression、full 减 eager-schema 的严格成功率百分点差；三次先按任务取均值，任务级配对 percentile bootstrap 固定种子 20261012、10,000 次、95% 区间。六题三次仍是六个独立任务；退化区间不支持确定性结论。

事前压力子集来自冻结合同，失败和无触发任务仍纳入；实际 Layer1、Layer2、发现、故障覆盖另报。失败归因以原始终止、独立条件和工具轨迹为依据，人工抽查另存，不改原评分。全部结果只解释为所选题库下的条件证据，不称官方全榜成绩，接受零或负收益。
