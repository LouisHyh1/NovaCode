# 项目记忆与会话持久化 Plan

## 1. 目标与边界

本章在 NovaCode 现有 Agent Loop、Textual TUI、模块化 prompt 和 ch08 上下文压缩之上增加四项能力：四层项目指令、追加式 JSONL 会话、每轮自动记忆提取、受门控的记忆治理。实现以文件系统和 Python 标准库为主，不复制 Agent Loop、文件工具、工具结果落盘或上下文压缩机制。

全局约束如下：

- 产品与包名统一为 `NovaCode`、`novacode`、`NovaCodeApp`。
- 用户目录使用 `~/.novacode/`，项目目录使用 `<project_root>/.novacode/`。
- 指令文件固定为 `NOVACODE.md` 与 `NOVACODE.local.md`。
- 消息文件固定为 `.novacode/sessions/<session_id>.jsonl`；工具结果继续位于 `.novacode/sessions/<session_id>/tool-results/`。
- 不引入 SQLite、向量库、embedding、相似度搜索、RAG 记忆检索或完整 Slash Command 框架。
- 指令、记忆与后台服务提供空值默认；`SessionWriter` 是接收消息的必选依赖，初始化或 model 绑定失败时不得进入可提交消息的状态。没有指令、记忆或历史会话时保持 ch08 行为。

## 2. 当前架构映射

| 当前入口 | 现状 | 本章最小改动 |
| --- | --- | --- |
| `src/novacode/conversation.py` | 线程安全地维护 `Message` 列表，已有 `replace_history()` | 增加“提交前”持久化钩子和 `from_messages()`；钩子成功后才修改内存 |
| `src/novacode/compact/` | `manage_context()` 完成工具结果落盘、摘要和 `replace_history()` | 保留算法；历史替换继续走 `Conversation.replace_history()`，由同一持久化钩子写压缩事务 |
| `src/novacode/compact/state.py` | `SessionContext` 只含 UUID 与 `spill_dir` | 改为可读 session ID，并显式保存消息路径和工具结果目录 |
| `src/novacode/prompt/__init__.py`、`modules.py` | 已预留 `自定义指令`、`长期记忆` 可选模块 | 给可选模块传入实际文本，仍由现有 priority 排序 |
| `src/novacode/agent/__init__.py` | Agent Loop 直接向 `Conversation` 写 user/assistant/tool 消息 | 继续使用这些方法；持久化失败转为可见错误；最终回复事件携带可提取的一轮数据 |
| `src/novacode/tui/commands.py`、`app.py` | 只有 `/exit`、`/plan`、`/do`、`/compact` | 只增加本章需要的 `/resume` 入口和恢复选择态 |
| `src/novacode/cli.py` | 负责配置、权限、工具、MCP 和 TUI 启动 | 按规定顺序创建指令、会话、记忆与后台服务并注入 `NovaCodeApp` |

## 3. 模块边界

### 3.1 `novacode.instructions`

只负责定位四个固定指令文件、按低到高优先级拼接、展开独占行 `@<relative_path>`，以及记录不含文件正文的诊断。它不监听文件变化，不参与 prompt 排序，也不读取任意第五个根指令位置。

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstructionLoader:
    project_root: Path
    user_root: Path
    max_reference_depth: int = 5

    def load(self) -> str: ...

    def _expand(
        self,
        path: Path,
        boundary: Path,
        depth: int,
        chain: tuple[Path, ...],
    ) -> str: ...
```

`load()` 固定按以下顺序处理，跳过不存在或内容为空的层，并只在相邻非空层之间插入独占行 `---`：

1. `~/.novacode/NOVACODE.md`；
2. `<project_root>/NOVACODE.md`；
3. `<project_root>/.novacode/NOVACODE.md`；
4. `<project_root>/NOVACODE.local.md`。

`_expand()` 的 `depth=0` 表示根文件，引用目标以 `depth + 1` 进入；第 5 层目标内容可以展开，但其中新的引用只记录深度警告。`chain` 是当前递归链而不是全局集合，因此环路被跳过，同一文件在不同分支仍可复用。用户级根文件的 `boundary` 为规范化后的 `~/.novacode/`，其余根文件为规范化后的 `<project_root>`；引用目标先 `resolve()`，再用 `Path.is_relative_to()` 校验边界。缺失、不可读、越界、环路、超深和含空字节的二进制目标均局部跳过并记录结构化日志。

### 3.2 `novacode.session`

该子包只处理消息 JSONL、会话枚举、恢复和清理。工具结果正文仍由 ch08 管理；会话扫描不读取这些正文。

```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from novacode.llm import Message


class SessionWriteError(OSError): ...


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    title: str
    model: str
    last_activity: datetime
    file_size: int
    path: Path


@dataclass
class SessionLoadResult:
    session_id: str
    messages: list[Message]
    model: str
    last_activity: datetime | None
    diagnostics: list[str] = field(default_factory=list)


class SessionWriter:
    def __init__(
        self,
        sessions_dir: Path,
        session_id: str,
        model: str,
    ) -> None: ...

    @classmethod
    def open_existing(
        cls,
        sessions_dir: Path,
        session_id: str,
        model: str,
    ) -> "SessionWriter": ...

    @property
    def path(self) -> Path: ...

    def bind_model(self, model: str) -> None: ...
    def append_message(self, message: Message) -> None: ...
    def append_compaction(self, replacement: list[Message]) -> None: ...
    def close(self) -> None: ...


def list_sessions(sessions_dir: Path) -> list[SessionInfo]: ...
def load_session(path: Path) -> SessionLoadResult: ...
def clean_expired(
    sessions_dir: Path,
    now: datetime,
    max_age: timedelta = timedelta(days=30),
) -> None: ...
```

`SessionWriter.path` 必须计算为 `<sessions_dir>/<session_id>.jsonl`。构造器以 append 文本模式打开该文件，不创建同名消息子目录；`compact.state.SessionContext.spill_dir` 独立指向 `<sessions_dir>/<session_id>/tool-results/`。新会话允许以空 model 构造 writer，但此时 writer 处于未绑定状态；provider 选择成功后、开放输入前必须调用 `bind_model(model)`。该方法在 writer 锁内拒绝空值、首条记录写入后的首次绑定和与既有绑定不一致的重复绑定。`append_message()` 在未绑定时直接抛出 `SessionWriteError`，首条消息从已绑定值写入 `model`。`open_existing()` 用已恢复的首条记录 model 建立已绑定 writer。`append_message()` 与 `append_compaction()` 共用一个 `threading.RLock`，每个完整 JSON 行执行 `write`、`flush`、`os.fsync` 后才返回。序列化或任一步写盘失败均抛出 `SessionWriteError`，调用方不得更新内存。

普通消息行至少包含 `type="message"`、`role`、UTC ISO 8601 `ts`，并按形态保存 `content`、完整 `tool_calls` 或完整 `tool_results`；第一条消息额外保存 `model`。tool call 保留 `id`、`name`、原始 JSON 参数，tool result 保留 `tool_call_id`、正文、`is_error` 和策略错误状态。

`append_compaction()` 为替换历史计算稳定摘要（规范 JSON 的 SHA-256）与唯一 `transaction_id`，依次追加：

1. `compact_begin`：事务 ID、替换记录数、摘要、时间；
2. 每条 `compact_message`：事务 ID、序号和完整消息；
3. `compact_commit`：相同事务 ID、记录数、摘要、时间。

只有 commit 行完成 `fsync` 后方法才成功返回。恢复器从头顺序解释日志；只有 begin、连续完整的替换记录和 commit 三者的事务 ID、数量、序号、摘要全部一致时，才用该替换历史取代事务前历史。未提交、损坏或不一致的事务整体忽略。

`load_session()` 对无法解析、类型未知或字段非法的单行记录诊断后继续。得到最后一个有效压缩状态后，再校验工具链：assistant 的 `tool_calls` 必须由紧随其后的 tool 消息按相同 ID 和顺序完整返回；未闭合、部分返回或顺序不匹配时从发起该链的 assistant 之前截断，孤立 tool 结果从该结果之前截断。`list_sessions()` 扫描且只扫描 `*.jsonl`，使用最后一条有效记录的活动时间倒序排序；`SessionInfo.title` 是首条有效 user 消息的单行截断摘要，没有有效 user 消息时固定为 `（无用户消息）`；没有任何有效消息的文件不返回。`clean_expired()` 使用相同活动时间，同时删除过期 JSONL 与同 ID 工具结果目录；单项失败记录后继续。

### 3.3 `novacode.memory`

记忆层将“模型判断”和“执行器约束”分开：模型决定是否值得保存、重复或冲突；执行器只接受固定类型、固定路由和目录内文件名。两个 `MEMORY.md` 仅存索引，正文各自存于独立 Markdown 文件。

```python
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from novacode.llm import Provider


class MemoryKind(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass(frozen=True)
class MemoryAction:
    action: Literal["create", "update", "delete", "no-op"]
    kind: MemoryKind | None = None
    memory_id: str = ""
    title: str = ""
    summary: str = ""
    content: str = ""
    filename: str = ""


@dataclass(frozen=True)
class MemoryTurn:
    user_content: str
    assistant_content: str


@dataclass(frozen=True)
class ApplyReport:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    rejected: int = 0


class MemoryStore:
    def __init__(
        self,
        directory: Path,
        allowed_kinds: frozenset[MemoryKind],
    ) -> None: ...

    @property
    def lock(self) -> asyncio.Lock: ...
    def read_index_locked(self) -> str: ...
    def apply_locked(self, actions: list[MemoryAction]) -> ApplyReport: ...
    def render_index_locked(self) -> str: ...


class MemoryExtractor:
    def __init__(
        self,
        provider: Provider,
        user_store: MemoryStore,
        project_store: MemoryStore,
        on_index_changed: Callable[[str], None],
    ) -> None: ...

    def submit(self, turn: MemoryTurn) -> None: ...
    async def run(self) -> None: ...
    async def close(self) -> None: ...


class MemoryGovernor:
    def __init__(
        self,
        sessions_dir: Path,
        stores: tuple[MemoryStore, ...],
        run_restricted_agent: Callable[..., Awaitable[list[MemoryAction]]],
        notify: Callable[[str], None],
    ) -> None: ...

    def maybe_schedule(self, now: datetime) -> bool: ...
    async def close(self) -> None: ...
```

固定路由为：`user`、`feedback` 只允许用户级 store，`project`、`reference` 只允许项目级 store。目标层级由 `MemoryKind` 推导，模型无权另传目录。记忆文件 frontmatter 至少写入稳定 UUID、`type`、`title`、`created`、`updated`；正文必须是可独立理解的陈述，时间敏感信息使用绝对日期，索引项包含类型、标题、简述和相对链接。

`MemoryStore` 的 `lock` 是提取与治理共用的进程内目录锁。调用方按“用户级、项目级”固定顺序获取两个锁，避免死锁；`read_index_locked()` 和 `apply_locked()` 只允许在持锁状态调用。`apply_locked()` 先在内存中按 delete、update/merge、create 顺序构造候选文件集和候选索引，再同时检查最多 200 行、UTF-8 大小不超过 25KB。某一操作超限时只拒绝该操作并回到它执行前的候选状态；磁盘上从未出现超限索引，也不触发治理兜底。合法批次通过同目录临时文件与 `os.replace()` 提交；越界路径、非法类型、错误路由、缺失目标和非法文件名被拒绝。

`MemoryExtractor.submit()` 只做 `asyncio.Queue.put_nowait()`。`run()` 是唯一消费者，严格按提交顺序处理：固定顺序获取两个 store 锁，重新读取最新索引，将最近一轮 user/final assistant、两级完整索引、固定路由和 create/update/delete/no-op 约束发送给 provider，且 `Request.tools=[]`；随后解析、校验、容量预演和提交，最后刷新 prompt 使用的两级索引快照。LLM、解析或写入失败只记录日志并释放两个锁，下一项继续。

`MemoryGovernor` 只编排门控、锁、受限子 Agent 和通知，不自行增加记忆事实。治理目标是合并重复、删除过时、修正有证据的矛盾、把相对日期改成绝对日期，并使索引满足双限额。治理使用与提取相同的 store 锁和提交校验。

治理仍要求五道门控全部通过，但 `maybe_schedule()` 为正确维护扫描状态按以下唯一顺序求值：

1. 先读取旧 `_last_scan_at` 检查 10 分钟节流；未满 10 分钟时直接返回且不更新时间，因为本次没有进入实际扫描；
2. 通过节流后立即把 `_last_scan_at` 更新为 `now`，从此本次属于“已扫描”，即使后续门控失败也保留该值；
3. 检查至少一个目标 memory 目录已存在；
4. 检查 `.consolidate-lock` 的最近成功 mtime 距当前至少 24 小时；
5. 检查 `list_sessions()` 返回的可用会话至少 5 个；
6. 尝试获取 `.consolidate-lock` 的跨进程运行态占用。

其中步骤 1–2 共同实现“距上次扫描至少 10 分钟”门控，步骤 3–6 是其余四道门控；任何一项失败都不创建后台任务。

锁文件保存运行中 PID，mtime 表示最近一次成功治理。获取时先尝试原子独占；已有 PID 确认存活时绝不抢占，确认死亡时原子回收，只有平台无法可靠判断 PID 时才以锁龄超过 1 小时作为回收条件。获取后保存原 mtime；成功时清除运行态 PID、写入新成功 mtime并释放占用，失败、取消或异常时恢复旧 mtime后释放。跨进程占用由锁文件的非阻塞文件锁实现，Windows 使用 `msvcrt`、POSIX 使用 `fcntl`，均为标准库；PID 与 mtime 决定陈旧状态，OS 文件锁保证并发检查最多一个持有者。

受限子 Agent 只拿到会话 JSONL 和两级 memory 目录的只读能力，以及目标 memory 目录的受约束写入能力；不注册 shell，不暴露源码写入或边界外路径。任务通过 `asyncio.create_task()` 后台运行，不阻塞启动和输入。完成或失败只通知状态与 create/update/delete 计数，不输出记忆正文。

## 4. 现有模块接口调整

### 4.1 `Conversation`：统一实现磁盘先于内存

```python
from collections.abc import Callable

BeforeAppend = Callable[[Message], None]
BeforeReplace = Callable[[list[Message]], None]


class Conversation:
    def __init__(
        self,
        before_append: BeforeAppend | None = None,
        before_replace: BeforeReplace | None = None,
    ) -> None: ...

    @classmethod
    def from_messages(
        cls,
        messages: list[Message],
        before_append: BeforeAppend | None = None,
        before_replace: BeforeReplace | None = None,
    ) -> "Conversation": ...
```

所有 `add_*()` 先构造完整 `Message`，在 `Conversation` 的 `RLock` 内调用 `before_append(message)`，成功后才 append 深拷贝。`replace_history()` 同理先调用 `before_replace(messages)`，成功后才替换内存；默认钩子为空时保持现有行为。`NovaCodeApp` 将当前 `SessionWriter.append_message` 与 `append_compaction` 绑定为钩子。这样 TUI 用户消息、Agent assistant/tool 消息和 ch08 历史替换共享一个提交点，不建立平行写入路径。

恢复时先用 `open_session_context()` 构造目标 `SessionContext`，再为目标会话新建完整 `SessionRuntime`：全新的 `ContentReplacementState`、`RecoveryState`、`CompactCircuitBreaker`，目标 session，归零的 `usage_anchor` 与 `anchor_msg_len`，以及按活动时间计算的 `resume_reminder`。恢复压缩显式把该目标 runtime 传给 ch08 压缩入口，绝不复用临时会话的 session-scoped 状态。随后用 `Conversation.from_messages()` 装入已校验历史并绑定目标 writer；初始装入不回写旧消息。

会话切换只有一个时序：先在旧会话仍有效时完整准备并验证新 writer、目标 runtime 与新 `Conversation`；准备失败则关闭已创建的新资源并保持旧会话。准备成功后进入短切换临界区，禁止新的消息提交，并原子替换 `conv`、writer 和整个 `SessionRuntime` 引用，随后恢复向新会话提交。退出临界区后再等待旧 writer 已进入的 append 完成并关闭旧 writer；此时关闭失败只记录日志，不能回切已经可能接收新消息的状态。

### 4.2 `compact.state`：统一 session ID 与关联路径

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    message_path: str
    spill_dir: str


def new_session_id(now: datetime | None = None) -> str: ...
def new_session_context(workspace: str) -> SessionContext: ...
def open_session_context(workspace: str, session_id: str) -> SessionContext: ...
```

`new_session_id()` 使用本地时间 `YYYYMMDD-HHMMSS` 加 4 位随机小写十六进制后缀。`new_session_context()` 创建工具结果目录并返回同 ID 的消息路径；`open_session_context()` 只接受格式合法且消息文件存在的 ID，不重命名或迁移旧存档。ch08 继续只使用 `spill_dir`。

### 4.3 prompt：填充既有可选槽位

```python
def optional_modules(
    instructions: str = "",
    memory_index: str = "",
) -> list[Module]: ...


def build_system_prompt(
    instructions: str = "",
    memory_index: str = "",
) -> str: ...
```

`自定义指令` 保持 priority 80，`长期记忆` 保持 priority 100；空内容由 `assemble_system()` 现有逻辑省略。长期记忆文本固定按用户级索引在前、项目级索引在后拼接，非空层之间用清晰标题分隔，只注入 `MEMORY.md`，不注入独立正文。指令在启动时加载并缓存；记忆索引只在启动和成功提交后刷新。

### 4.4 Agent 与 TUI：窄接口注入

`Agent` 新增可选的 `instructions: str` 与 `memory_index: Callable[[], str]`，每轮开始仍调用现有 `prompt.build_system_prompt()`，从 callable 获取最新合规索引。`run_force_compact()` 增加可选 `runtime: SessionRuntime | None = None`；普通压缩使用当前 runtime，恢复候选压缩必须显式传入刚创建的目标 runtime。`Event` 增加可选 `memory_turn: MemoryTurn | None`；只有无待执行工具调用的最终回复分支填充它。持久化异常转为 `Event.err`，不再继续请求模型或写内存。

`NovaCodeApp` 新增 `project_root`、`session_context`、必选 `SessionWriter`、可选 `MemoryExtractor`、可选 `MemoryGovernor` 与缓存索引字段。provider 选择成功后先调用 `writer.bind_model(provider.model)`，成功才创建 Agent 并开放输入；绑定失败显示可处理错误并保持输入禁用。`_dispatch()` 捕获用户消息写盘失败，失败时不渲染用户气泡、不启动 Agent。消费到最终事件时先渲染回复并恢复输入，再对非空 `memory_turn` 调用 `MemoryExtractor.submit()`；入队是同步常数时间操作，提取不阻塞下一轮。

`commands.py` 只注册 `/resume`；`resume.py` 负责把 `SessionInfo` 转为 `OptionList` 项、处理选择和调用 app 的恢复方法。`SessionState` 增加 `RESUMING`，不扩展通用命令解析器。

超过 24 小时的恢复提醒不写入 JSONL，也不伪装成 user 消息。`SessionRuntime` 保存可选 `resume_reminder`，Agent 将其通过现有 `Request.reminder` 作为 system reminder 注入当前恢复上下文；会话切换时替换，创建新会话时清空。

## 5. 五条数据流

### 5.1 启动加载

```text
cli._amain()
  -> resolve project_root and sessions_dir
  -> compact.new_session_context(project_root)
       -> session ID + message_path + tool-results path
  -> InstructionLoader(project_root, ~/.novacode).load()
  -> create user/project MemoryStore
       -> read two MEMORY.md indexes if present
  -> SessionWriter(sessions_dir, session_id, model="")
       -> failure: show session initialization error and return nonzero; do not enter TUI
  -> build prompt from cached instructions + indexes
  -> create MemoryExtractor worker
  -> NovaCodeApp(... all optional services ...)
  -> schedule clean_expired(days=30)
  -> MemoryGovernor.maybe_schedule() lazy check
  -> enter interactive TUI
```

provider 选择成功后、输入框可用前，`NovaCodeApp` 原子调用 `writer.bind_model(provider.model)`；首条消息只从该绑定值写入 model，多 provider 选择不会产生虚假消息。指令或索引缺失按空内容兼容；`SessionWriter` 初始化失败是会话启动错误，CLI 显示错误并返回非零，绝不启动可接收消息但无法持久化的 TUI。只有后台清理、提取和治理失败可隔离记录且不阻断交互。

### 5.2 消息追加

```text
TUI or Agent constructs Message
  -> Conversation.add_*()
       -> acquire Conversation RLock
       -> SessionWriter.append_message()
            -> acquire writer RLock
            -> serialize one full JSON line
            -> append -> flush -> fsync
            -> release writer RLock
       -> append deep copy to in-memory history
       -> release Conversation RLock
  -> render or continue Agent Loop
```

writer 抛错时 `Conversation` 不变。用户消息失败则不渲染且不启动请求；assistant 或 tool 消息失败则本轮以可见错误收尾。退出或恢复切换先禁止新提交，等待 writer 锁内操作结束，再关闭句柄，保证不会跨 session ID 写入。

### 5.3 恢复会话

```text
/resume
  -> list_sessions(<sessions_dir>/*.jsonl)
  -> user selects SessionInfo
  -> load_session(info.path)
       -> skip bad rows
       -> apply last valid committed compact transaction
       -> truncate at last complete tool-chain boundary
  -> build detached Conversation.from_messages(messages)
  -> open_session_context(original ID) and create a fresh target SessionRuntime
  -> estimate tokens with ch08 estimator
       -> over safe threshold: run existing compact flow with target runtime
       -> failure: show error, keep current session unchanged
  -> open SessionWriter with selected original ID
  -> if detached history was compacted:
       writer.append_compaction(candidate.messages())
  -> create live Conversation.from_messages(candidate, writer hooks)
  -> if last_activity > 24 hours: set ephemeral system reminder
  -> prepare and validate new writer + full runtime + Conversation
  -> short critical section: block new submits and atomically swap all three references
  -> resume submits to the new session
  -> wait for in-flight old append and close old writer; log close failure without rollback
  -> continue appending to original JSONL and tool-results directory
```

恢复前默认创建的新会话文件不自动删除。压缩在 detached candidate 上完成并显式使用目标会话的全新 runtime，只有压缩成功且压缩事务 commit 已 `fsync` 后才进入切换临界区；切换前失败不会改变当前会话。原子切换后沿用原 session ID 和全新 session-scoped 状态，旧 writer 关闭失败只降级记录，不回滚已切换状态。

### 5.4 自动提取

```text
Agent reaches final response with no pending tool calls
  -> persist assistant Message, then update Conversation
  -> TUI renders final response and re-enables input
  -> MemoryExtractor.submit(MemoryTurn) with put_nowait
  -> one consumer dequeues in submission order
  -> acquire user store lock, then project store lock
  -> reload latest two MEMORY.md indexes
  -> provider Request(messages=[extraction prompt], tools=[])
  -> parse create/update/delete/no-op actions
  -> validate kind, derived route, filename and boundaries
  -> simulate delete -> update/merge -> create and both index limits
  -> atomically commit accepted files and indexes
  -> refresh prompt index snapshot
  -> release both locks and process next item
```

每个最终回复都提交一次，不使用轮数或关键词门槛。队列中的后一项必须等前一项成功提交或失败收尾后才开始推理；锁覆盖“最新索引读取—LLM 推理—校验—提交”的完整区间。失败不修改会话历史，也不终止主 Agent。

### 5.5 记忆治理

```text
startup or later lazy check
  -> old _last_scan_at within 10 minutes? return without update
  -> set _last_scan_at = now for this actual scan
  -> memory directory exists?
  -> last successful mtime at least 24 hours old?
  -> at least 5 recoverable sessions?
  -> acquire .consolidate-lock with PID/mtime rules?
  -> asyncio background task
       -> acquire target MemoryStore lock(s) in fixed order
       -> start restricted child Agent
            read: session JSONL + user/project memory
            write: target memory directory only
            no shell, no source write, no outside path
       -> validate and commit through MemoryStore
       -> success: update lock mtime
       -> failure/cancel: restore prior mtime
       -> release store and cross-process locks
       -> notify only status and operation counts
```

任一门控失败立即返回且不创建后台任务。扫描节流状态与成功治理 mtime 分开，失败治理不会延后下一次满足 24 小时门槛的机会。

## 6. 错误与并发策略

| 场景 | 一致性边界 | 降级与可观测性 |
| --- | --- | --- |
| 指令根文件或引用不可读 | 只影响该文件或该引用 | 记录路径与原因，不记录正文，继续其他层 |
| 引用越界、环路、超深、二进制 | 不注入目标内容 | 结构化 warning，启动继续 |
| 普通消息序列化、append、flush 或 fsync 失败 | 内存不接受该消息 | 抛 `SessionWriteError`；用户可见错误，本轮停止 |
| 压缩事务中断 | 内存不执行该次替换；恢复忽略整笔未提交事务 | 保留事务前完整历史并记录诊断 |
| JSONL 单行损坏或末行截断 | 跳过该行，后续有效行继续读取 | 会话仍可恢复并续写 |
| 工具链不完整或错序 | 截断到此前最后完整边界 | 日志记录截断原因和 ID，不输出工具正文 |
| 恢复压缩失败 | 当前 TUI 会话和目标存档均不切换 | 显示错误，允许重新选择或取消 |
| 单个过期会话删除失败 | 其他清理项继续 | 分项日志；不阻塞启动 |
| 提取模型、解析、容量或写入失败 | 当前队列项失败，锁释放；主会话不变 | 结构化日志，继续下一项 |
| 普通提取导致索引超限 | 只拒绝导致超限的操作 | 保留该操作前文件与索引，不启动治理 |
| 提取与治理同时写同一目录 | 共用 `MemoryStore.lock` 串行 | 固定双锁顺序避免死锁 |
| 两进程同时治理 | 非阻塞文件锁与 PID 状态最多放行一个 | 未获锁者门控失败并返回 |
| 治理失败或取消 | 恢复获取前 mtime | 简短失败通知，不包含记忆正文 |
| 会话切换准备失败 | 尚未进入引用交换临界区 | 关闭新资源并保持旧会话，不改变任何活动引用 |
| 会话切换后的旧 writer 关闭失败 | 新引用已经原子生效 | 记录日志且不回切，后续消息只进入新 writer |
| 退出 | 停止新 append，等待临界区，关闭当前 writer | 取消或收尾绑定旧项目的后台任务，禁止写错目标 |

后台任务由 `NovaCodeApp` 统一持有引用。退出时：先停止新输入，关闭提取队列并等待当前项到安全提交点或取消，取消治理并按失败语义恢复锁 mtime，等待清理任务结束或取消，最后关闭 writer。切换项目或会话不复用旧项目的 extractor/governor；仅恢复同项目会话时沿用 memory 服务。

## 7. 文件布局

```text
src/novacode/
├── instructions/
│   ├── __init__.py          # 导出 InstructionLoader
│   └── loader.py            # 四层加载、独占行引用、深度/环路/边界/二进制检查
├── session/
│   ├── __init__.py          # 导出公开会话接口
│   ├── types.py             # SessionInfo、SessionLoadResult、SessionWriteError
│   ├── codec.py             # Message 与 JSONL 记录编解码、压缩摘要
│   ├── writer.py            # SessionWriter、append、flush/fsync、压缩事务
│   ├── reader.py            # 坏行隔离、事务恢复、工具链截断
│   ├── listing.py           # *.jsonl 扫描与有效活动时间排序
│   └── cleanup.py           # 30 天后台清理与局部失败隔离
├── memory/
│   ├── __init__.py          # 导出类型、store、extractor、governor
│   ├── types.py             # MemoryKind、MemoryAction、MemoryTurn、ApplyReport
│   ├── store.py             # 固定路由、目录锁、frontmatter、索引双限额与原子提交
│   ├── extractor.py         # 单消费者队列、无工具 LLM 提取、完整临界区
│   ├── governor.py          # 五门控、PID/mtime 锁、受限后台治理与通知
│   └── prompts.py           # 提取与治理的结构化提示和解析约束
├── conversation.py         # 提交前钩子、from_messages、replace_history 复用
├── compact/
│   ├── compact.py           # 继续通过 Conversation.replace_history 提交历史
│   └── state.py             # 可读 session ID、message_path、spill_dir、恢复上下文
├── prompt/
│   ├── __init__.py          # build_system_prompt(instructions, memory_index)
│   └── modules.py           # 填充现有两个可选模块
├── agent/
│   └── __init__.py          # prompt 索引提供器、持久化错误、最终轮提取事件
├── tui/
│   ├── commands.py          # 仅注册 /resume
│   ├── resume.py            # 恢复列表与选择交互
│   └── app.py               # writer/memory 生命周期、RESUMING 状态、原子切换
└── cli.py                   # 按启动顺序装配服务与后台任务

tests/
├── instructions/test_loader.py
├── session/test_writer.py
├── session/test_reader.py
├── session/test_listing_cleanup.py
├── memory/test_store.py
├── memory/test_extractor.py
├── memory/test_governor.py
├── test_conversation.py
├── test_prompt.py
├── test_agent.py
└── test_tui.py
```

`codec.py` 只集中无状态编解码，避免 writer、reader 重复字段规则；其余拆分均对应独立并发或错误边界。除此之外不新增仓储层、事件总线或数据库抽象。

## 8. 技术决策

| 决策 | 选择 | 原因 |
| --- | --- | --- |
| 指令合并 | 四个固定位置，低优先级在前 | 后置高优先级内容可覆盖前置约束，来源边界明确 |
| 引用语法 | 仅独占行 `@<relative_path>` | 避免把正文中的 `@` 误当引用 |
| 会话格式 | 单文件追加式 JSONL | 可逐行恢复、局部隔离损坏、无需数据库 |
| 消息一致性 | `Conversation` 提交前钩子 | 所有入口统一实现磁盘先于内存，未启用时零行为变化 |
| 压缩持久化 | begin/messages/commit 事务 | 追加语义下识别完整替换，崩溃时可回到事务前历史 |
| 恢复压缩 | detached candidate 上复用 ch08 | 压缩失败不污染当前会话或目标存档 |
| 工具链恢复 | 严格 ID 与顺序校验后截断 | 不把模型置于无法继续的半完成工具状态 |
| 记忆路由 | 类型到用户级/项目级的固定映射 | 模型只能提出语义操作，不能选择任意路径 |
| 记忆去重 | LLM 读取最新完整索引后判断 | 不增加第二套模糊检索机制 |
| 索引容量 | 锁内候选预演，逐操作拒绝 | 永不落盘超限文件，且允许 delete/update 先释放容量 |
| 提取并发 | 每项目一个单消费者队列 | 保证每轮都处理且后一轮读取前一轮最新结果 |
| 治理并发 | store 锁 + 跨进程治理锁 | 同目录写入互斥，同时避免多进程重复治理 |
| 过期提醒 | 现有 `Request.reminder` | 维持 system 语义，不篡改历史消息文件 |
| 历史入口 | 一个 `/resume` 命令与一个 TUI 状态 | 满足主动恢复，不扩展通用命令框架 |

## 9. 需求覆盖与验证

| Spec 范围 | Plan 落点 |
| --- | --- |
| F1–F8 | 3.1 指令位置、顺序、分隔、引用深度、访问链、边界、容错、prompt 缓存 |
| F9–F14 | 3.2 session ID 关联路径、消息结构、磁盘先行、追加锁、压缩事务 |
| F15–F21 | 3.2 与 5.3 会话列表、坏行、工具链、压缩、原 ID 续写、24 小时提醒、30 天清理 |
| F22–F30 | 3.3 与 5.4 四类路由、独立文件、索引双限额、每轮队列、无工具请求、LLM 去重、完整锁区间 |
| F31–F37 | 3.3 与 5.5 治理职责、五门控、扫描节流、PID/mtime、受限子 Agent、后台通知 |
| F38–F42 | 4、5.1、6 prompt 模块、启动/每轮/退出生命周期、现有模块复用与空配置兼容 |

实现阶段按模块写单元测试，并在集成完成后执行全量 pytest。重点断言如下：

- 四层指令顺序、独占行引用、5 层边界、分支复用、真实路径越界和二进制拒绝；
- session ID 与两种关联路径、每次 append 的 flush/fsync、写盘失败不改内存、未提交压缩事务回滚；
- 坏行继续读取、完整工具链保留、两类不完整边界截断、按记录活动时间列表、首条有效 user 摘要及固定降级标题和 30 天清理；
- 恢复超阈值先压缩、失败不切换、超过 24 小时仅注入 system reminder、原 ID 续写；
- 四类记忆固定路由、frontmatter 和相对链接、create/update/delete/no-op、200 行与 25KB 双限额；
- 连续最终回复逐轮入队、单消费者顺序、后一项读取最新索引、提取请求工具列表为空；
- 五道治理门控、10 分钟扫描节流、活/死/未知 PID、1 小时兜底、并发只获一锁、失败恢复 mtime；
- 受限子 Agent 拒绝 shell、源码和边界外写入，治理期间主会话可继续输入，通知不含正文；
- 无指令、无 memory、无历史或后台任务失败时仍能启动；writer 初始化/model 绑定失败时禁止消息提交；切换/退出无跨 session 写入且切换后旧 writer 关闭失败不回滚。

最终按项目约定在 tmux 中启动 NovaCode，执行真实多轮对话、工具调用、压缩、退出与 `/resume`，并对照本章 Checklist 逐项验收。文档本身先执行旧名称/错误路径扫描与 `git diff --check`，确保 Plan 只描述当前或明确计划新增的 NovaCode 模块。
