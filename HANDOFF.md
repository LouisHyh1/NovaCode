# NovaCode 交接：Stage 12完成，产品版本0.1.39

2026-10-17，add-reproducible-agent-evaluation 的实施任务 **78/78**。第12阶段真实产品tmux、隔离生命周期、完整质量门禁及69个规范场景审阅通过，见 [专项报告](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-12/product-validation.md)。当前用户授权验证后提交推送并停止；不自动同步主spec、归档change或继续实验。

## 本轮变更与验证

修复Session恢复菜单选择或取消后输入焦点未回到ChatInput的问题，两条产品按键回归先失败后通过。首轮真实模型失败现场和费用保留，第二轮真实tmux完成read_file、write_file审批拒绝、Session创建/退出/重启恢复、历史回忆、流式中断及资源关闭。共10次Provider调用，Token已知下界22585，取消请求usage未知1次，金额未知；全部与主集分账。

170项隔离Fake/机制测试覆盖记忆显式操作、后台任务提交/取消、共享Provider所有权与退出等待；没有真实模型长期记忆或多Agent效果实验。操作者真实项目记忆前后哈希一致。1060项完整pytest、uv锁、Ruff/格式、严格mypy/增量复杂度、架构和启动通过。pytest明确仅收集tests/，避免历史源码快照的同名模块冲突；新增专项脚本已纳入scripts/validate_change.py。原始账本、终端捕获、Session工件、失败和验证收据均在stage-12/。

## 主集历史身份与结果

v3主集仍为 **FORMAL_COMPLETE / REPORT_REVIEWED**：24道独立任务、216/216次、严格成功147/216（68.06%）、未开始零。[最终主集报告](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/formal-v3-20261017/publication-0.1.38/最终评测报告.md)及其原报告输入保持原字节，本轮重建四份核心报告逐字节一致。

受测版本 **0.1.37**，发布提交 **02b2da5187ed56890d710e9b039a2a7dbc703245**，campaign **novacode-compact-20261017-v3-r1**，manifest SHA-256 **0cf2d3fb6ba40d1f4f62b17d31cdb975c19eb5b2adb0ac89d0572922f703fa2f**。0.1.38是主集结果文档发布，当前0.1.39是产品专项修复；均不改变主集受测版本。原231个绑定源码文件在publication-0.1.38/execution-snapshot/保存，before-publication.json记录映射。当前实时冻结检查因版本/源码变化拒绝是预期行为；历史复核用原哈希快照，不能关闭指纹检查启动旧模型队列。

主集终止为completed174、budget-exhausted34、timeout8；Token已知下界54196141，208/216次成本完整，8个请求usage未知，金额未知。卸载36次，摘要请求与接受为零，不声称Layer2摘要收益。每来源12个独立任务，三重复不是独立题目；主要配对区间含零，父池已查看，不称全新留出集或完整官方榜单。用户授权1GiB门槛和宿主内存监控为单列条件变更，不声称环境全程相同。Windows原生交互专项本轮未重做，不声称全平台生产就绪。

## 原始现场与执行边界

旧v2仍为147次终态和285条未开始，不追认完成，不重跑或混算。保留停止容器novacode-eval-45b72375695f4bd0912308e1c2cfb0e5，不启动或删除。全部现存原始工件、.tmp/formal-compact-v3-20261017/结果/账本/归档/baselines、.tmp/compact-v3-deployment-20261016/和外部缓存运行时均保留，不按临时目录名清理。Git证据不是全量本机原始工件备份。

不读取、审查或修改docs/。不提交.tmp、操作者配置或真实记忆。后续每次提交须递增版本；任何新模型实验需要独立有效身份、范围和授权，不能借用已结束的计划键。归档或同步规范留待用户后续明确指令。
