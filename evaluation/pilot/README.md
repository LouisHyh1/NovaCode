# 开发先导与校准

仅测量已入库的 24 道开发题，不读取冻结任务结果，不生成 432 行冻结计划。运行复用产品装配、正式 Agent 与 `run_task()`，本目录只承担顺序编排、复核和预算校准。

`policy.json` 是首个调用前的有限合同。普通任务为 300 秒、600,000 Token、24 Provider/50 工具请求，单请求预留 100,000 Token；长上下文为 420 秒、1,500,000 Token、40/70，单请求预留 240,000；清理宽限均 20 秒。同题三组相同，总上限为 30,000 秒、60,000,000 Token、2,200 Provider/4,200 工具请求。总量预留覆盖 72 次原计划、三次单题 tmux 和三个用量探针，失败或未知用量不退还整次预留。目录必须新建，代码、资产或策略改变不得覆盖已开始运行。

请求端点保持 `https://api.deepseek.com/anthropic`，请求名为 `deepseek-v4-flash`。2026-10-12 用户同意按官方当前映射记录服务为 DeepSeek-V4.1-Flash。响应仍可能返回兼容别名；不能将它称为旧模型或不可变权重版本。应用窗口沿用产品既有 200,000 Token；端点支持 1M，所有配置使用相同的应用上限。SDK 零重试、90 秒超时、输出 4096、thinking=true；适配器仅在无工具历史时发送 thinking，但端点在省略时仍默认开启思考。发送的 budget_tokens=2048 被兼容端点忽略，实际思考块与结束原因单独记录。

下面操作会调用真实模型；仅在已授权的第九阶段范围和 finite 合同核验后执行。`ROOT` 是全新持久缓存目录，控制器可在独立 tmux 会话中顺序运行，输出日志保存到该目录。setup 与 review 不调用模型。

```bash
.venv/bin/python evaluation/pilot/run.py setup --root "$ROOT"
.venv/bin/python evaluation/pilot/run.py probe --root "$ROOT"
.venv/bin/python evaluation/pilot/run.py representative --root "$ROOT"
# 人工核验 S01、L02 六条真实账本和清理后，显式扩展剩余开发题。
.venv/bin/python evaluation/pilot/run.py remaining --root "$ROOT"
.venv/bin/python evaluation/pilot/run.py tmux --root "$ROOT"
.venv/bin/python evaluation/pilot/review.py --root "$ROOT" --output "$EVIDENCE"
```

三配置每题运行一次，共 72 次原计划；顺序按固定种子排列，六种配置顺序各使用四次。失败、超时、未知 usage、范围及清理分别保留，不取最好结果。tmux 的三组 S01 对话与主测量分列。原始 `result.json`、Provider/工具账本、最终回答、请求产物、补丁及独立判题无损压缩交付；报告能从账本重建，准备/主端到端/判题分别计时。

金额使用当日官方峰值价计算上界，输入未缓存 $0.3、缓存读 $0.006、输出 $1.2／百万 Token。该值不等于实际账单；未知 usage 或非零缓存创建量使金额保持未知，已知 Token 下界另报。原始用量探针用公开重复文本，对照两次 Anthropic 与一次原生接口；探针关闭 thinking，仅验证缓存计数，不能充当主集推理成本证据。[官方模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)、[兼容规则](https://api-docs.deepseek.com/guides/anthropic_api/)。

第九阶段先导已完成；数据与边界见 [交付报告](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-9/pilot-validation.md)。旧 UTF-8 字节除以 3.5 的输入预留在真实请求上低估，原轨迹保留。当前 Provider 用请求 UTF-8 字节作保守准入估计，真实 usage 仍是唯一结算来源；六次 S01/S08 新源码复验不替代首轮，且与首轮共享不退还的有限总预留。

`calibrated-budgets.json` 用全部三组原样本、失败/预算删失下界和新源码六次复验的最大观测乘 1.5，单请求按完整请求字节加输出上限，清理至少 20 秒；依据规划中各类别冻结配额预留 432 次，再保留 10% 总余量。它只形成 `CALIBRATED_NOT_FROZEN` 数值合同，不赋予正式执行状态；第十阶段仍须完成新题库、家族隔离与全部身份核验。Layer2 真实触发为零，不宣称摘要效果。
