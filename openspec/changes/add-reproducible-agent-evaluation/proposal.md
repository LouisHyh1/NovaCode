## Why

NovaCode 已有机制测试和真实模型 smoke 入口，但尚无固定任务合同、独立判题、完整成本记录及冻结实验协议，不能据此判断实际任务成功率或上下文治理的收益。本 change 建立以工程改进和回归为主、兼顾可复核面试证据的评测体系，并实现已确认消融所需的压缩开关和工具 Schema 按需暴露。

## What Changes

- 建立两条验收轨道：Python、WSL/Linux 上的单主 Agent 编程任务主集，以及 TUI、权限、Session、长期记忆、多 Agent、Windows 兼容性的独立专项验收。
- 先建设 24 个开发任务：12 个固定版本 SWE-bench-Live `verified` 修复任务、12 个自建专项任务；扩展为 24 个开发任务与 48 个冻结任务，Live 和自建任务各占一半。开发集难度目标为 8 简单、12 中等、4 困难，难度与机制压力分开标记。
- 新增自动化评测入口，复用正式 Agent 主循环及必要装配；每次运行使用隔离工作区、真实模型、无人辅助和独立自动验收，另以 tmux 验证代表性任务与产品入口的一致性。
- 为主调用、摘要、重试、工具调用、故障恢复及资源关闭建立统一观测；Token 缺失记为未知，补齐当前摘要成本遗漏。
- 新增默认开启的统一上下文压缩策略开关，覆盖 Layer 1、Layer 2、自动、紧急、手动和恢复入口。
- 实现基础工具始终可见、非基础内置及 MCP 工具 Schema 按需发现与暴露；工具能力和权限不随曝光方式改变，连接生命周期不延迟。产品兼容默认仍为全量暴露，实验完整方案显式开启按需暴露。
- 固定 `deepseek-v4-flash`、Anthropic 协议、`thinking=true`；主 Agent 与摘要调用一致。冻结任务在完整方案、关闭压缩、全量 Schema 三种配置下各独立运行三次，共 432 个计划运行；预算由开发集先导测量后按类别冻结。
- 分别报告 Live 子集与自建专项的成功率、工具调用、完整 Token、端到端时间、失败原因和机制触发覆盖；重复运行不充当独立题目，不要求消融结果必须为正。

## Capabilities

### New Capabilities

- `agent-evaluation-corpus`：任务合同、独立验收、Live 入库、自建题库、分层与开发/冻结集隔离。
- `agent-evaluation-runner`：正式主循环复用、隔离运行、预算约束、终止分类与 tmux 一致性验收。
- `agent-evaluation-observability`：全调用账本、Token 语义、工具关联、时间边界和失败证据。
- `agent-evaluation-experiments`：先导校准、冻结、432 运行计划、配对消融、统计和报告边界。
- `context-compression-policy`：统一压缩开关和各入口的一致策略行为。
- `progressive-tool-schema`：工具发现、按轮更新 Schema、曝光状态与权限等价性。

### Modified Capabilities

无。既有 TUI 运行链、模块职责、权限边界、真实摘要 Hook、Provider 单一所有者与质量门禁继续适用；新增能力不放宽这些要求。

## Impact

后续实施预计涉及 `src/novacode/agent/`、`compact/`、`tool/`、`llm/`、`config.py`、`session/`、CLI 装配及相应测试；评测编排和资产建议放在独立的 `src/novacode/evaluation/`、`evaluation/` 与测试目录中，核心模块不得反向依赖评测包。Live 环境需要固定的官方判题代码、任务容器和独立验收进程，本机适用性在前置阶段核验。

本次交付仅生成规划文档和术语，不实现上述功能、不发起模型调用、不运行正式评测、不提交 Git。所有文件写入本 change 与根 `CONTEXT.md`；不读取、修改或引用项目 `docs/`，保留既有 README 修改。详细任务候选见 [task-catalog.md](task-catalog.md)，实施决策见 [design.md](design.md)，执行与冻结协议见 [evaluation-protocol.md](evaluation-protocol.md)。
