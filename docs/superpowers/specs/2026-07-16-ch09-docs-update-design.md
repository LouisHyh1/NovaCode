# 第九章文档更新设计

## 目标

依据飞书《理论学习：跨会话记忆与会话持久化》，更新 `docs/ch09` 中的 Spec、Plan、Tasks、Checklist，使四篇文档：

- 完整覆盖理论篇的项目指令、会话持久化、自动记忆和记忆治理；
- 使用当前项目名称与 Python 包结构；
- 相互引用一致，可直接作为后续实现和验收依据；
- 与现有 ch08 上下文压缩及 NovaCode 代码结构衔接。

## 命名与路径映射

| 理论篇旧标识 | NovaCode 文档标识 |
| --- | --- |
| MewCode | NovaCode |
| `mewcode` | `novacode` |
| `MewCodeApp` | `NovaCodeApp` |
| `MEWCODE.md` | `NOVACODE.md` |
| `MEWCODE.local.md` | `NOVACODE.local.md` |
| `.mewcode/` | `.novacode/` |
| `~/.mewcode/` | `~/.novacode/` |
| `src/mewcode/` | `src/novacode/` |
| `python -m mewcode` | `python -m novacode` |

文档不得残留作为产品名称、包名或有效路径的 MewCode 标识。解释来源差异时也不保留旧路径示例，统一写成 NovaCode 语义。

## 理论内容到文档的映射

### 1. 项目指令

指令文件按低优先级到高优先级加载并拼接：

1. `~/.novacode/NOVACODE.md`；
2. `<project_root>/NOVACODE.md`；
3. `<project_root>/.novacode/NOVACODE.md`；
4. `<project_root>/NOVACODE.local.md`。

高优先级内容排在后面。引用语法采用理论篇的独占行 `@<relative_path>`，不继续使用现有文档自拟的 `@include`。递归引用包含五层深度限制、访问链环路检测、项目或用户目录边界检查、缺失文件容错和二进制文件拒绝。

### 2. 会话持久化

每个会话使用 `.novacode/sessions/<YYYYMMDD-HHMMSS-xxxx>.jsonl`，不再设计为“会话目录 + conversation.jsonl”。ch08 工具结果仍放在 `.novacode/sessions/<session_id>/tool-results/`，两种数据通过相同 session ID 关联。

每条消息先持久化再进入内存，采用追加写、flush 和 fsync。恢复时逐行解析并跳过损坏行，验证工具调用链，只保留最后一个完整边界；超出上下文阈值时复用 ch08 压缩；距上次活动超过 24 小时时插入过期上下文提醒。列表和清理均按 JSONL 记录的最后活动时间判断，30 天未活动会话后台清理。

### 3. 自动记忆

四类记忆及位置固定为：

- `user`、`feedback`：`~/.novacode/memory/`；
- `project`、`reference`：`<project_root>/.novacode/memory/`。

每条记忆使用带 YAML frontmatter 的独立 Markdown 文件，`MEMORY.md` 只保存可点击的相对路径索引。索引限制为 200 行且不超过 25KB；注入内容仅为索引，具体正文按需通过文件工具读取。

每轮 Agent Loop 得到最终回复后都在后台尝试提取，而不是“每五轮或命中关键词才触发”。提取请求包含最近一轮对话、现有记忆清单和明确的 create/update/delete/no-op 约束，不携带工具定义。去重、冲突判断和是否值得保存交给模型；失败只记录日志，不中断当前会话。

### 4. 记忆治理

新增独立治理流程，负责合并重复记忆、删除过时内容、修正矛盾、把相对日期改成绝对日期并修剪索引。

治理采用懒检查：记忆目录存在、距上次成功治理至少 24 小时、距离上次扫描至少 10 分钟、累计会话不少于 5 个、成功获取 `.consolidate-lock` 后才启动。锁保存 PID，并以 mtime 作为最近治理时间；死进程或超过一小时的锁可回收，治理失败需恢复旧 mtime。

实际整理在后台受限子 Agent 中运行。它只能读取会话和记忆资料，写入范围限于对应 memory 目录。治理完成后向主会话发送简短结果通知。

## 四篇文档职责

- `Spec.md`：定义背景、范围、功能需求、非功能需求、不做事项和可验证验收标准。
- `Plan.md`：定义 Python 模块边界、核心数据结构、启动与运行时数据流、错误处理、文件布局和技术决策。
- `Tasks.md`：把 Plan 拆成按依赖排序的文件级任务；每项列出修改文件、实现步骤和最小验证命令。
- `Checklist.md`：逐项覆盖 Spec 的验收标准，并给出自动化测试及 tmux 端到端场景。

四篇文档使用同一术语和编号体系。Tasks 中的每项必须能追溯到 Spec 功能，Checklist 必须覆盖项目指令、会话写入与恢复、自动记忆、记忆治理和完整启动流程。

## 当前代码适配原则

- 复用 `src/novacode/compact/`、`Conversation.replace_history()`、现有 prompt 模块槽位和 Textual TUI，不复制一套平行机制。
- 模块路径以当前仓库为准，例如 `src/novacode/agent/__init__.py`，不假设不存在的旧版 `agent/agent.py`。
- 新功能按职责放入 `instructions/`、`session/`、`memory/`，但不为单一实现增加抽象接口或外部依赖。
- 本次只更新四篇文档，不修改运行时代码，不执行实现阶段的 tmux 验收。

## 文档验证

更新完成后执行：

1. 搜索 `MewCode`、`mewcode`、`MEWCODE`、`.mewcode`，确认四篇文档无旧名称残留；
2. 搜索当前不存在或错误的关键路径，核对模块清单；
3. 核对 Spec、Plan、Tasks、Checklist 的术语、阈值、文件布局和触发条件一致；
4. 核对四层指令优先级、24 小时提醒、30 天清理、200 行/25KB 索引、治理五道门控均有对应验收项；
5. 运行 `git diff --check -- docs/ch09` 检查 Markdown 空白错误。

## 明确不做

- 不照搬 Go 类型或伪代码接口；
- 不引入 SQLite、向量数据库、embedding 或相似度搜索；
- 不扩展为完整 Slash Command 框架；
- 不在本轮实现第九章代码；
- 不改动 `docs/ch09` 以外的既有章节文档。
