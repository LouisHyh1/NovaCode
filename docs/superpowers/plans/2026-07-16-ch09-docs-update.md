# 第九章四篇文档更新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `docs/ch09` 的 Spec、Plan、Tasks、Checklist 改写为与飞书理论篇一致、适配当前 NovaCode Python 仓库且可直接指导后续实现的完整文档集。

**Architecture:** 先以 Spec 固化统一需求，再由 Plan 映射到当前 Python 模块，Tasks 拆成文件级实施步骤，Checklist 逐项验证 Spec。四篇文档共享同一命名、阈值、文件布局和触发语义，最后用全文搜索和结构核对防止旧名称及互相矛盾的约束残留。

**Tech Stack:** Markdown、PowerShell、ripgrep、Git；运行时背景为 Python 3.11+、Textual、现有 `novacode` 包。

## Global Constraints

- 产品名、包名和路径统一使用 `NovaCode`、`novacode`、`NovaCodeApp`、`NOVACODE.md`、`NOVACODE.local.md`、`.novacode/`。
- 本次只修改 `docs/ch09` 四篇文档，不修改 `src/`、`tests/`、配置文件或其他章节。
- 项目指令按用户级、项目根、项目私有目录、本地覆盖的低到高优先级拼接，高优先级内容排在后面。
- 指令引用语法为独占行 `@<relative_path>`，最大递归深度为 5 层。
- 会话存档为 `.novacode/sessions/<YYYYMMDD-HHMMSS-xxxx>.jsonl`；ch08 工具落盘目录仍为 `.novacode/sessions/<session_id>/tool-results/`。
- 会话超过 24 小时未活动时恢复需注入时间跨度提醒，超过 30 天未活动时后台清理。
- 自动记忆类型固定为 `user`、`feedback`、`project`、`reference`；每轮最终回复后异步尝试提取。
- `MEMORY.md` 索引最多 200 行且不超过 25KB。
- 记忆治理门控固定包含：目录存在、24 小时时间门、10 分钟扫描节流、至少 5 个会话、获取锁成功。
- 不引入 SQLite、向量数据库、embedding、相似度搜索或完整 Slash Command 框架。

---

## File Structure

- `docs/ch09/项目记忆与会话持久化 Spec.md`：唯一需求来源，定义功能、非功能约束、范围和验收标准。
- `docs/ch09/项目记忆与会话持久化 Plan.md`：把 Spec 映射为当前 NovaCode Python 模块、数据结构和运行流程。
- `docs/ch09/项目记忆与会话持久化 Tasks.md`：给出按依赖排序的文件级实现任务和验证命令。
- `docs/ch09/项目记忆与会话持久化 Checklist.md`：覆盖所有验收标准和 tmux 端到端场景。

### Task 1: 重写 Spec，建立统一需求基线

**Files:**
- Modify: `docs/ch09/项目记忆与会话持久化 Spec.md`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-07-16-ch09-docs-update-design.md` 中的命名映射、理论内容映射和适配原则。
- Produces: 编号为 F1 起的统一功能需求和编号为 AC1 起的验收标准，供 Plan、Tasks、Checklist 引用。

- [ ] **Step 1: 用九个固定章节重建 Spec 结构**

按以下顺序写入章节，不保留旧版“三层指令”“每五轮触发”“会话目录 + conversation.jsonl”等内容：

```markdown
# 项目记忆与会话持久化 Spec

## 背景与问题
## 目标
## 功能需求
### 项目指令文件
### 会话持久化
### 会话恢复与清理
### 自动记忆
### 记忆治理
### 集成与生命周期
## 非功能需求
## 不做的事
## 验收标准
```

- [ ] **Step 2: 写清四层项目指令及安全规则**

功能需求必须明确四个加载位置、低到高优先级、`---` 分隔、独占行 `@<relative_path>`、五层递归、当前访问链去环、根边界检查、缺失文件注释和二进制拒绝；指令与记忆分别填入当前 prompt 的 `自定义指令` 与 `长期记忆` 可选模块。

- [ ] **Step 3: 写清 JSONL 会话模型和恢复语义**

需求必须明确：单个 JSONL 文件、`role/content/ts` 和工具调用字段、先写磁盘再更新内存、追加写与 fsync、坏行跳过、工具调用链完整边界、复用 ch08 压缩、24 小时提醒、30 天清理，以及 JSONL 会话文件与 tool-results 目录共享 session ID 但路径不同。

- [ ] **Step 4: 写清自动记忆和治理语义**

需求必须明确四类记忆的固定落盘层级、独立 Markdown 文件及 frontmatter、相对链接索引、200 行/25KB 上限、每轮最终回复后异步提取、create/update/delete/no-op、无工具请求、LLM 去重，以及治理的五道门控、PID/mtime 锁、失败恢复 mtime、受限后台子 Agent 和完成通知。

- [ ] **Step 5: 生成逐项可验证的 AC**

验收标准至少覆盖：四层优先级、引用安全、JSONL 追加与崩溃恢复、工具链截断、24 小时提醒、30 天清理、四类记忆路由、每轮提取、索引限制、治理门控、锁回收、受限写入和主流程不阻塞。

- [ ] **Step 6: 检查 Spec 自洽性**

Run:

```powershell
rg -n "三层|@include|每 5 轮|conversation\.jsonl|6 小时|project_knowledge|user_preference" "docs/ch09/项目记忆与会话持久化 Spec.md"
```

Expected: 无输出。

### Task 2: 重写 Plan，映射到当前 NovaCode 架构

**Files:**
- Modify: `docs/ch09/项目记忆与会话持久化 Plan.md`

**Interfaces:**
- Consumes: Task 1 的 F/AC 需求；现有 `src/novacode/compact/`、`src/novacode/conversation.py`、`src/novacode/prompt/`、`src/novacode/agent/__init__.py`、`src/novacode/tui/`、`src/novacode/cli.py`。
- Produces: 模块边界、Python 数据结构、时序、错误处理和文件布局，供 Tasks 拆解。

- [ ] **Step 1: 固化模块边界与职责**

Plan 必须包含下列模块，不引用不存在的 `agent/agent.py` 或旧包路径：

```text
src/novacode/instructions/  # 四层指令加载与 @ 引用
src/novacode/session/       # JSONL writer、reader、list、cleanup
src/novacode/memory/        # types、store、extractor、governor、prompts
src/novacode/conversation.py
src/novacode/compact/state.py
src/novacode/prompt/__init__.py
src/novacode/prompt/modules.py
src/novacode/agent/__init__.py
src/novacode/tui/commands.py
src/novacode/tui/app.py
src/novacode/tui/resume.py
src/novacode/cli.py
```

- [ ] **Step 2: 定义足够实现的 Python 接口**

Plan 至少定义 `InstructionLoader`、`SessionWriter`、`SessionInfo`、`MemoryKind`、`MemoryAction`、`MemoryStore`、`MemoryExtractor`、`MemoryGovernor` 的关键字段和签名；会话路径必须是 `<sessions_dir>/<session_id>.jsonl`，不能把 JSONL 放回 session 子目录。

- [ ] **Step 3: 写出五条端到端数据流**

分别写清：启动加载、消息追加、恢复会话、自动提取、记忆治理。每条流程必须标明调用顺序、内存与磁盘更新顺序、后台任务边界、失败时的降级行为。

- [ ] **Step 4: 写出错误处理和并发策略**

明确 Writer 使用线程锁保护追加；提取和治理各自串行；治理跨进程使用 `.consolidate-lock`；读取坏行、缺失记忆和单个过期会话删除失败均局部降级；任何后台失败不得终止 Agent 主会话。

- [ ] **Step 5: 核对 Plan 路径和阈值**

Run:

```powershell
rg -n "src/mewcode|MewCodeApp|\.mewcode|conversation\.jsonl|6h|每 5 轮|@include" "docs/ch09/项目记忆与会话持久化 Plan.md"
```

Expected: 无输出。

### Task 3: 重写 Tasks，形成可执行的文件级任务

**Files:**
- Modify: `docs/ch09/项目记忆与会话持久化 Tasks.md`

**Interfaces:**
- Consumes: Task 2 的模块、接口和数据流。
- Produces: 后续代码实现可逐项执行的任务清单，每项带精确文件、依赖、步骤和验证命令。

- [ ] **Step 1: 按依赖重新分组任务**

任务顺序固定为：session ID 与路径契约、Conversation 持久化钩子、指令加载、会话读写、会话恢复 UI、记忆存储、自动提取、记忆治理、prompt/Agent/CLI 集成、自动化测试、tmux 端到端验收。

- [ ] **Step 2: 为每个任务列出精确文件**

每个任务必须使用 `src/novacode/`、`tests/` 下的实际或计划新增路径，并明确 Create/Modify/Test；不得出现 `.mewcode/config.yaml.example` 等仓库当前不存在且理论篇不要求的附带工作。

- [ ] **Step 3: 把理论约束写进对应任务步骤**

四层优先级只出现在指令任务，会话先落盘只出现在 Writer/Conversation 任务，24 小时与 30 天分别落到恢复和清理任务，200 行/25KB 落到 Store，五道门控与锁语义落到 Governor，避免同一规则在多处出现不同值。

- [ ] **Step 4: 为每项给出最小验证命令**

验证命令以计划新增测试文件为单位，例如：

```powershell
pytest tests/test_instructions.py -q
pytest tests/test_session.py -q
pytest tests/test_memory.py -q
pytest tests/test_memory_governor.py -q
pytest tests/test_tui.py tests/test_agent.py -q
```

- [ ] **Step 5: 核对任务依赖图无环且覆盖全部 Plan 模块**

Run:

```powershell
rg -n "^## T|\*\*文件|\*\*依赖|\*\*验证" "docs/ch09/项目记忆与会话持久化 Tasks.md"
```

Expected: 每个 T 编号都有文件、依赖和验证字段。

### Task 4: 重写 Checklist 并完成跨文档验证

**Files:**
- Modify: `docs/ch09/项目记忆与会话持久化 Checklist.md`
- Verify: `docs/ch09/项目记忆与会话持久化 Spec.md`
- Verify: `docs/ch09/项目记忆与会话持久化 Plan.md`
- Verify: `docs/ch09/项目记忆与会话持久化 Tasks.md`

**Interfaces:**
- Consumes: Task 1 的 AC 和 Task 3 的验证命令。
- Produces: 自动化检查、人工观察和 tmux 真实对话相结合的最终验收清单。

- [ ] **Step 1: 按功能域编排 Checklist**

使用以下章节：静态质量、项目指令、会话写入、会话恢复与清理、自动记忆、记忆治理、集成、tmux 端到端。每个条目都写明操作、预期结果和对应 AC 编号。

- [ ] **Step 2: 增加记忆治理专项场景**

至少覆盖：未满 24 小时不运行、10 分钟内不重复扫描、少于 5 个会话不运行、活锁拒绝、死锁/超时锁回收、失败恢复 mtime、只能写 memory 目录、成功通知主会话。

- [ ] **Step 3: 更新 tmux 端到端场景**

场景至少覆盖：冷启动创建 JSONL、四层指令冲突与本地覆盖、`@` 引用、工具链存档、崩溃恢复、24 小时提醒、30 天清理、四类记忆跨会话生效、治理整理重复/过时记忆、压缩后恢复。所有命令和路径使用 NovaCode 名称。

- [ ] **Step 4: 执行旧名称与旧设计残留检查**

Run:

```powershell
rg -n -i "MewCode|mewcode|MEWCODE|\.mewcode|@include|每 5 轮|6 小时|conversation\.jsonl" docs/ch09
```

Expected: 无输出。

- [ ] **Step 5: 执行关键理论约束覆盖检查**

Run:

```powershell
rg -l "NOVACODE\.local\.md" docs/ch09
rg -l "24 小时" docs/ch09
rg -l "30 天" docs/ch09
rg -l "200 行" docs/ch09
rg -l "25KB" docs/ch09
rg -l "10 分钟" docs/ch09
rg -l "5 个会话" docs/ch09
rg -l "\.consolidate-lock" docs/ch09
```

Expected: 每条命令至少命中 Spec、Plan、Tasks、Checklist 中承担该约束的相关文档；不存在只有 Spec 提到而执行与验收遗漏的约束。

- [ ] **Step 6: 检查 Markdown 和最终差异范围**

Run:

```powershell
git diff --check -- docs/ch09
git status --short
```

Expected: `git diff --check` 无输出；变更范围只有四篇 `docs/ch09/*.md`，忽略用户原有 `.tmp/` 噪声。

- [ ] **Step 7: 提交四篇文档**

```powershell
git add -- "docs/ch09/项目记忆与会话持久化 Spec.md" "docs/ch09/项目记忆与会话持久化 Plan.md" "docs/ch09/项目记忆与会话持久化 Tasks.md" "docs/ch09/项目记忆与会话持久化 Checklist.md"
git commit -m "docs: align ch09 with NovaCode memory theory"
```
