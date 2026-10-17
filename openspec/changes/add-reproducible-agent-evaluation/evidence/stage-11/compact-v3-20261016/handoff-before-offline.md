# NovaCode 交接：可复现评测文档修订完成，尚未开始恢复与修复

交接日期：2026-10-16。本文所有仓库路径均相对当前项目根目录。用户明确要求在当前目录生成本文件，因此覆盖 handoff 技能默认保存到操作系统临时目录的位置规则。

## 当前交接点

当前 change 为 `add-reproducible-agent-evaluation`。最近完成的是文档修订与静态校验，状态为 `DOCUMENTS_READY`，不是历史审计完成、缺陷已修复或新实验已冻结。用户本轮只要求生成交接文档；不要因此自动继续实施或启动进程。

下一会话若获用户指示继续实施，应从 `tasks.md` 的 **11.7 保全清单**开始，先只读检查现场。当前实际复选框进度为 61/78，新增 11.7–11.15 九项全部未完成；旧文档中的 71 项等数字是历史口径。不要把已勾选的旧首块或统计实现当作新 campaign 已验收。

## 用户决策和执行边界

用户先否决直接重跑 432 次或继续旧计划剩余 285 次，要求保留现有 147 次终态和全部现存原始工件，优先尝试离线恢复 11 次缺失业务补丁，再审计全部历史结果的可靠性。随后要求修复相关缺陷、补充回归并验证代表任务的提取、归档恢复和独立判题链路，倾向建立独立的精简新 campaign。具体规则和预算已经写入协议，不在本文重复。

最近的范围限定是先修改全部相关文档，完成前不进行实验。文档已完成，但这不授权真实模型实验；正式模型调用仍等待用户明确确认。不得恢复旧模型队列，不得覆写或删除原结果、原归档、原账本及保留工作区。历史派生恢复和复核也必须另建输出，不能改原评分或混入新主结果。

本次文档轮没有执行真实模型、离线恢复重放、代表独立判题、任务抽选或重新冻结，没有修改实现，没有提交推送。下一会话要区分“规划写好了”和“实施已经做了”。

## 首先阅读的权威文件

| 文件 | 用途 |
|---|---|
| [当前协议](openspec/changes/add-reproducible-agent-evaluation/evaluation-protocol.md) | 执行 v3 规划，含历史隔离、抽样、恢复等级、修复测试、预算和授权边界；第 10–12 节是此次修订重点 |
| [任务计划](openspec/changes/add-reproducible-agent-evaluation/tasks.md) | 下一未完成步骤与验证要求，按依赖推进，不擅自勾选 |
| [设计](openspec/changes/add-reproducible-agent-evaluation/design.md) | 决策 10–12 说明恢复、基线/归档修复及新冻结边界 |
| [文档修订记录](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/protocol-revision-20261016/documentation-update.md) | 本轮交付与验证范围，包含哪些工作尚未完成 |
| [机器可读校验](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/protocol-revision-20261016/validation.json) | 11 份现行文档指纹、静态校验和 61/78 进度 |
| [修订前保存清单](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/protocol-revision-20261016/preservation.json) | 修改前文档快照及 6,116 个受保护源码/历史文件哈希 |
| [中断审计](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/interruption-20261015/interruption-recovery.md) | 已有诊断、原始缺口和第 147 次恢复经过；末尾旧方案选择已被当前协议替代，原文不要改 |
| [缺失补丁清单](openspec/changes/add-reproducible-agent-evaluation/evidence/stage-11/interruption-20261015/artifact-gaps.json) | 原始缺失与第 147 次补充恢复的区分 |
| [正式执行说明](evaluation/formal/README.md) | 当前禁用旧队列、已有实现局限及历史/新计划关系 |

提案、任务目录及四份评测规格已同步修订；完整修改范围由上述保存/校验清单给出。现有压缩和工具发现策略不在本次修复中调参。

## 原始现场定位与恢复陷阱

旧运行根为 `.tmp/formal-stage11-v2/`，原终态位于 `runs/<run_id>/`，原归档位于 `archives/<run_id>.tar.gz`，执行身份在 `identity.json`。不要清空 `.tmp`；这些是目前实际历史证据，不能当作可删除缓存。原冻结文件位于 `evaluation/frozen/campaign-v2/`，开发与冻结资产保持原身份。

第 147 次目录为 `.tmp/formal-stage11-v2/runs/novacode-frozen-20261015-v2-FL20-2-no-compression/`，其中保留 `workspace/`、`recovery.jsonl`、`recovery-submission.diff`、`initial-ignored-recovered.json.gz` 和恢复说明。补充恢复补丁不等于原验收已改判，也尚未通过本轮新增独立链路复核。旧 11 次缺失补丁不能仅凭本文件或旧报告认定永久不可恢复，应按当前协议尝试并保存完整性缺口。

上次只读检查时，模型调度器和 worker 未运行，原停止容器 `novacode-eval-45b72375695f4bd0912308e1c2cfb0e5` 仍保留。该信息不是下一会话实时进程状态；先检查进程和容器，不能直接启动。原 `.tmp/stage11-continuation.md` 包含更早的过程记录和过期队列状态，不能优先于当前协议和此交接点。

现行协议路径已由 v2 更新为 v3 规划，旧实时冻结核验出现协议指纹不一致是预期的停止条件。修订前同哈希协议在保存清单的 `before/` 下；后续历史复核须显式解析到原快照及原执行资产，不能改旧 manifest、绕过校验或误以为旧 campaign 可继续启动。

已有缺陷定位和复现路径见中断审计；实现入口是 `src/novacode/evaluation/artifacts.py`、`src/novacode/evaluation/runner.py`、`evaluation/formal/run.py`，新规模相关入口还有 `src/novacode/evaluation/frozen.py`、`evaluation/formal/report.py`、`evaluation/formal/status.py`。这些只是后续调查入口；本轮没有修复代码，不运行 `run.py` 或 `refreeze.py`。

## 验证证据及边界

本轮 `openspec validate add-reproducible-agent-evaluation --strict` 和 `git diff --check` 通过，文档本地链接、UTF-8/表格、预算算术、唯一任务编号及已勾选任务内容核验通过。保存清单中 6,116 个受保护文件的哈希未变，147 份终态及 147 份归档仍在。这不是全量历史可靠性审计；第 147 次保留业务工作区没有纳入本轮递归散列，不能将该静态比对宣传为 11.7–11.9 完成。

本轮没有执行新的代码回归、真实 tmux、离线独立判题或模型可用性探测。较早会话的完整测试和冻结验收只支持当时对应身份，不替代修复后的新验证。若后续检查 ledger，避免直接输出巨大 `preflight_passed.initial_ignored` 清单，先解析成计数和摘要；不要输出凭证、认证头或完整用户配置。

## 工作区和工具使用

当前分支为 `eval/reproducible-agent-evaluation`，最后提交为 `3854954`（v0.1.35 冻结）。存在更早阶段未提交的实现、0.1.36 版本改动、v2 资产和本次文档；不要将所有脏改动都归因于本轮，也不要还原、清理或广泛暂存。若以后用户要求提交，先复核补丁版本递增及 `pyproject.toml`、运行时版本、`uv.lock` 和已安装元数据的一致性，显式暂存并审计暂存差异。本轮交接不执行提交或推送。

遵守根 `AGENTS.md`：中文回答及新注释；项目 `docs/` 不读取、不审查、不修改，也不作为事实来源。不要为模型诊断引入未授权的真实调用。用户未要求并行委派，本次无需启动子 agent。

当前宿主为 PowerShell，项目位于 WSL UNC 工作区。Windows PowerShell 的 `openspec` 可用，WSL 的既定 PATH 中未找到该 CLI；使用 Windows 命令做 OpenSpec 静态操作。Linux 项目命令经 `wsl -d Ubuntu-22.04 -- ...`，使用仓库 `.venv/bin/python`。PowerShell 不支持 Bash 的 `{a,b}` 文件名展开，独立文件路径应明确列出。

以下是下一会话可先执行的只读入口，不启动实验：

```powershell
wsl -d Ubuntu-22.04 -- /usr/bin/git status --short
openspec status --change add-reproducible-agent-evaluation --json
openspec instructions apply --change add-reproducible-agent-evaluation --json
```

## Suggested skills（建议技能）

- 继续已授权的实现：调用仓库 `.codex/skills/openspec-apply-change/SKILL.md`，先检查当前任务和上下文，从 11.7 开始。
- 进一步修改既有协议或计划：调用仓库 `.codex/skills/openspec-update-change/SKILL.md`；仅修改获授权文档，保持各文件一致。
- 调查补丁与归档缺陷：调用本机 `diagnosing-bugs` 技能，先复现和解释根因，再改实现；复用已有证据，不掩盖合同问题。
- 实现通用修复：使用本机 `ponytail` 技能保持最小方案；回归覆盖协议要求的真实失败模式，不另建 Agent 引擎。
- 以后再次生成交接：使用 `handoff` 技能，保存位置以用户当次明确要求为准。
