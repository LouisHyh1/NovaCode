# NovaCode 交接：v3 主集 216 次完成，结果文档发布 0.1.38

2026-10-17 当前主集状态 **FORMAL_COMPLETE / REPORT_REVIEWED**：24 道独立任务、216/216 次已记录，严格成功 **147/216（68.06%）**，未开始为零。全部范围、清理和归档重建检查通过。整体任务 **74/78**；下一阶段为 tasks.md 的 Stage 12 四项产品专项，当前均未完成，不能因主集完成而勾选。

## 当前入口与身份

最终审阅报告：[openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/formal-v3-20261017/publication-0.1.38/最终评测报告.md](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/formal-v3-20261017/publication-0.1.38/最终评测报告.md)。原始自动报告、分来源统计、完整运行、失败归因和配对结果仍保存在同一证据树的 memory-resume-20261017/final-report/，旧部分报告与准备中断现场保留。

受测版本 **0.1.37**，发布提交 **02b2da5187ed56890d710e9b039a2a7dbc703245**，campaign **novacode-compact-20261017-v3-r1**，manifest SHA-256 **0cf2d3fb6ba40d1f4f62b17d31cdb975c19eb5b2adb0ac89d0572922f703fa2f**。本次版本 **0.1.38** 只发布结果与状态文档；不重写旧 manifest，不把本次代码版本当作当时受测版本。

冻结执行的 231 个绑定文件已在 publication-0.1.38/execution-snapshot/ 留存原字节，映射见 before-publication.json。当前版本及状态文档改变后，旧实时冻结核验被拒绝是预期行为；历史复核显式使用快照映射，不关闭指纹检查、不直接恢复旧模型队列。

## 结果与限制

终止：completed 174、budget-exhausted 34、timeout 8。Token 已知下界 54,196,141，208/216 次成本完整，8 个请求 usage 未知；金额未知。卸载 36 次，摘要请求和接受均为零，不能声称 Layer 2 摘要收益。Live 与专项各 12 独立任务，三次重复不是独立题目；主要配对区间含零，父题库结果已被查看，不称全新留出或完整官方榜单结果。

## 执行关闭与保全

2026-10-17 18:53 队列正常执行完全部原计划，Windows 状态 F:\NovaCode-evaluation-cache\memory-supervision-20261017\status.json 为 FINALIZED_COMPLETE / plan_executed。此前第 154 次后用户授权 WSL 门槛改为 1 GiB；两次准备中断无模型调用，后来内存改善并启用独立 Windows 监控后完成剩余 62 次。运行条件变化单列，不声称环境条件全程相同。最终 WSL 约 8.74 GiB 可用，没有因空间阈值停止。

原 v2 的 147 次终态、285 未开始及全部现存原始工件保持原字节；保留容器 novacode-eval-45b72375695f4bd0912308e1c2cfb0e5 不启动或删除。当前 .tmp/formal-compact-v3-20261017/ 的结果/账本/归档/共享 baselines 及 .tmp/compact-v3-deployment-20261016/、外部缓存运行时均保留。Git 中的报告输入不等于本机全量原始工件备份，不按临时目录名清理。

## 发布与后续

本轮已获用户“更新相应文档，并提交和推送”授权，补丁版本升至 0.1.38；仅明确暂存相关文档、版本及评测交付证据，实际提交与远端状态以 Git 为准。原 HANDOFF 与任务状态的发布前副本位于 publication-0.1.38/before-HANDOFF.md 与 before-tasks.md。

不读取、审查或修改 docs/。不提交 .tmp、用户配置或记忆，不修改旧清单和原评分。后续每次提交仍须递增版本；任何新模型运行需独立的有效冻结身份、范围和授权，不能借用已结束计划键重跑。
