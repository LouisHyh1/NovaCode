# 项目记忆与会话持久化 Tasks

> 本文把已批准的 Spec 与 Plan 拆成可逐项实施、逐项验证的文件级任务。按 T1 → T10 顺序执行；每个任务完成后，其产物必须满足后续任务列出的接口契约。

**目标：** 在不复制 ch08 压缩、工具结果落盘和 Agent Loop 的前提下，为 NovaCode 增加四层项目指令、追加式 JSONL 会话、主动恢复、每轮自动记忆提取与受门控记忆治理。

**技术栈：** Python 3.12、标准库文件系统/线程/异步原语、Textual、现有 NovaCode provider、pytest、ruff、tmux。

## 全局约束

- 产品、包和应用类固定使用 `NovaCode`、`novacode`、`NovaCodeApp`。
- 用户级数据位于 `~/.novacode/`，项目级数据位于 `<project_root>/.novacode/`。
- 消息文件固定为 `.novacode/sessions/<session_id>.jsonl`，工具结果固定为 `.novacode/sessions/<session_id>/tool-results/`。
- 不引入数据库、向量检索、embedding、RAG 记忆检索、完整 Slash Command 框架或第二套压缩格式。
- `SessionWriter` 是接收消息的必选依赖；初始化或 model 绑定失败时不得进入可提交消息的状态。
- 指令、记忆和后台服务保持可选；无对应文件或数据时维持 ch08 的交互行为。

---

## T1：统一 session ID、消息路径与工具结果路径

**文件：**
- Modify: `src/novacode/compact/state.py`
- Test: `tests/compact/test_state.py`
- Test: `tests/compact/test_layer1.py`
- Test: `tests/compact/test_layer2_manage.py`

**依赖：** 无。

**步骤：**
1. 按批准 Plan 的构造契约将 `SessionContext` 固定为 `session_id`、`message_path`、`spill_dir` 三个必选字段；`message_path` 指向 `<workspace>/.novacode/sessions/<session_id>.jsonl`，`spill_dir` 指向同 ID 目录下的 `tool-results/`，两者不能互相嵌套或改用不同 ID。同步把 compact 测试中现有两参数或位置参数构造改为显式传入三字段，避免用兼容默认值掩盖调用点遗漏。
2. 实现 `new_session_id(now: datetime | None = None) -> str`：使用本地时间生成 `YYYYMMDD-HHMMSS`，追加 `secrets.token_hex(2)` 产生的 4 位小写十六进制后缀；对注入的 `now` 保持确定性时间部分，随机后缀仍校验格式。
3. 修改 `new_session_context(workspace: str) -> SessionContext`：创建 sessions 根目录与 `spill_dir`，返回同一 session ID 对应的消息路径；不预写消息记录。
4. 新增 `open_session_context(workspace: str, session_id: str) -> SessionContext`：先用完整正则校验 ID，再要求消息文件已经存在；只打开原 ID 的关联路径，不重命名、不迁移、不创建替代消息文件。
5. 在 `tests/compact/test_state.py` 覆盖格式、同 ID 双路径、工具目录创建、非法 ID、消息文件缺失和恢复原路径；更新 `tests/compact/test_layer1.py` 与 `tests/compact/test_layer2_manage.py` 的 `SessionContext` 构造，并运行整个 `tests/compact/`，确认 ch08 的 Layer 1、Layer 2、token 恢复和端到端压缩仍只按既有职责消费 `spill_dir`。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/compact -q
```
预期：整个 compact 测试集合通过；生成 ID 匹配 `^\d{8}-\d{6}-[0-9a-f]{4}$`，消息文件路径和工具结果目录共享同一 ID，现有两参数构造点全部迁移到批准的三字段契约。

## T2：建立 SessionWriter 与 Conversation 的先盘后内存提交点

**文件：**
- Create: `src/novacode/session/__init__.py`
- Create: `src/novacode/session/types.py`
- Create: `src/novacode/session/codec.py`
- Create: `src/novacode/session/writer.py`
- Modify: `src/novacode/conversation.py`
- Test: `tests/session/test_writer.py`
- Test: `tests/test_conversation.py`

**依赖：** T1。

**步骤：**
1. 在 `types.py` 定义 `SessionWriteError(OSError)`；在 `codec.py` 集中实现 `Message` 与普通 JSONL 记录的无状态编解码，记录至少包含 `type="message"`、`role`、UTC ISO 8601 `ts`，并按消息形态保留 `content`、完整 tool call 或完整 tool result，其中首条消息额外写入已绑定 `model`。
2. 实现 `SessionWriter(sessions_dir: Path, session_id: str, model: str)`，其 `path` 必须为 `<sessions_dir>/<session_id>.jsonl`。构造器只以追加文本模式打开该文件；允许新会话以空 model 初始化，但此状态下 `append_message()` 必须抛 `SessionWriteError`。
3. 实现 `bind_model(model: str)`：在 writer 的 `threading.RLock` 内拒绝空值、首条记录之后的首次绑定及与已绑定值不一致的重复绑定。实现 `open_existing()`，使用恢复结果首条记录中的 model 建立已绑定 writer。
4. 实现 `append_message()`：持有同一个 writer 锁完成序列化、写入一整行、`flush`、`os.fsync`；任一步失败都统一抛 `SessionWriteError`。实现 `close()`：禁止后续追加，等待已进入锁区的追加完成后关闭句柄，多次关闭保持幂等。
5. 给 `Conversation` 增加 `before_append: Callable[[Message], None] | None` 和 `before_replace: Callable[[list[Message]], None] | None`。所有 `add_*()` 在 `Conversation` 的 `RLock` 内先构造完整消息、调用 `before_append`，成功后才深拷贝进内存；`replace_history()` 同样先调用 `before_replace`，成功后才替换。
6. 实现 `Conversation.from_messages()`：深拷贝装入已校验历史并绑定可选钩子，初始装入不得触发写盘。默认无钩子时保持当前行为。
7. 测试 user、assistant、tool 消息字段与顺序、首条 model、未绑定拒绝、bind 冲突、flush/fsync、并发串行、关闭语义，以及序列化/写入/flush/fsync 失败均不改变 `Conversation`；写入失败后磁盘允许残留完整或部分字节，禁止通过重写或截断 append-only 文件回滚，由 T4 恢复器把不完整或非法行当坏行隔离；另测 `from_messages()` 不回写旧历史。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation.py tests/session/test_writer.py -q
```
预期：全部通过；失败注入下 `Conversation` 内存不接受该条消息且调用方得到 `SessionWriteError`；磁盘可能保留完整合法行或部分字节，仅不完整或非法行由恢复器隔离，完整合法行可能在重启后被恢复，append-only 文件不做物理回滚；成功路径中完整行的 append、flush、fsync 先于内存变更。

## T3：实现四层项目指令加载与安全引用展开

**文件：**
- Create: `src/novacode/instructions/__init__.py`
- Create: `src/novacode/instructions/loader.py`
- Test: `tests/instructions/test_loader.py`

**依赖：** T2。

**步骤：**
1. 在 `loader.py` 定义不可变 `InstructionLoader(project_root: Path, user_root: Path, max_reference_depth: int = 5)`，并由 `__init__.py` 导出。
2. `load()` 只按低到高优先级读取 `~/.novacode/NOVACODE.md`、`<project_root>/NOVACODE.md`、`<project_root>/.novacode/NOVACODE.md`、`<project_root>/NOVACODE.local.md`；跳过缺失或展开后为空的层，只在相邻非空层之间插入独占行 `---`，加载结果由启动流程缓存。
3. `_expand()` 仅把匹配独占行 `@<relative_path>` 的整行识别为引用，行内 `@` 原样保留。引用相对声明文件目录解析，根文件深度为 0，第 5 层目标内容可展开，但其内部新引用只记录诊断并跳过。
4. 使用当前递归链而非全局集合检测环路，使同一文件可在不同分支重复展开。对用户级根使用规范化后的 `~/.novacode/` 为边界，其余三层使用规范化后的项目根；目标 `resolve()` 后用 `Path.is_relative_to()` 拒绝父目录、符号链接及等价路径越界。
5. 缺失、不可读、越界、环路、超深和含空字节的二进制目标只记录不含正文的结构化 warning，并继续其余层和其余引用；空文件返回空内容，单个失败不得中止启动。
6. 测试四层顺序与分隔、任意层缺失、独占行语法、5 层边界、分支复用、环路、真实路径越界、二进制和局部读取失败；断言加载器不扫描第五个根位置且不监听运行期变化。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/instructions/test_loader.py -q
```
预期：全部通过；四层内容按规定顺序出现，所有失败场景只跳过局部内容并产生无正文诊断。

## T4：完成 JSONL 压缩事务、恢复、会话列表与 30 天清理

**文件：**
- Modify: `src/novacode/session/__init__.py`
- Modify: `src/novacode/session/types.py`
- Modify: `src/novacode/session/codec.py`
- Modify: `src/novacode/session/writer.py`
- Create: `src/novacode/session/reader.py`
- Create: `src/novacode/session/listing.py`
- Create: `src/novacode/session/cleanup.py`
- Test: `tests/session/test_writer.py`
- Test: `tests/session/test_reader.py`
- Test: `tests/session/test_listing_cleanup.py`

**依赖：** T3。

**步骤：**
1. 在 `types.py` 定义 `@dataclass(frozen=True)` 的 `SessionInfo(session_id, title, model, last_activity, file_size, path)`；`SessionLoadResult(session_id, messages, model, last_activity, diagnostics)` 使用普通 `@dataclass`，其中 `diagnostics` 以 `field(default_factory=list)` 创建并允许 reader 逐行追加；从 `session.__init__` 导出 writer、reader、listing、cleanup 的公开接口。
2. 在 `codec.py` 增加规范 JSON 序列化与 SHA-256 稳定摘要。`SessionWriter.append_compaction(replacement)` 使用唯一事务 ID，并在同一 writer 锁内依次追加 `compact_begin`、带连续序号的全部 `compact_message`、`compact_commit`；每行都执行 write/flush/fsync，只有 commit 完成 fsync 后才返回成功。
3. `load_session(path)` 从头逐行解析；坏 JSON、未知类型或字段非法只追加诊断并继续后续有效行。压缩事务只有在 begin、连续完整替换消息和 commit 的事务 ID、数量、序号、摘要全部一致时才替换事务前历史；未提交、损坏或不一致事务整体忽略。
4. 在压缩状态解释完成后严格校验工具链：assistant 的 tool calls 只能由紧随其后的 tool message 按相同 ID 和顺序完整返回；未闭合、部分返回或错序时从发起链的 assistant 之前截断，孤立 tool result 从其自身之前截断，同时保留此前最后完整边界。
5. `list_sessions(sessions_dir)` 只扫描 `*.jsonl`，以最后一条有效记录的活动时间倒序排序；无有效消息的文件不返回。标题取首条有效 user 内容的单行截断摘要，没有有效 user 时固定为 `（无用户消息）`；同时返回首条 model、有效活动时间和文件大小，不读取工具结果正文。
6. `clean_expired(sessions_dir, now, max_age=timedelta(days=30))` 保持同步磁盘 worker：有有效记录时复用最后有效活动时间；无有效记录时使用 `max(JSONL mtime, 同 ID tool-results 目录 mtime)`，目录不存在则只取 JSONL mtime。仅删除严格超过 30 天的 JSONL 及同 ID 工具结果目录，单项删除失败记录后继续。另提供公开异步入口 `clean_expired_async()`，使用 `await asyncio.to_thread(clean_expired, ...)` 或行为清楚等价的线程包装，供后台 task 调度，不能在事件循环中直接执行同步扫描。
7. 按职责补充测试：`tests/session/test_writer.py` 覆盖普通往返、压缩三阶段成功、begin 后中断、部分替换中断及摘要/序号不一致；`tests/session/test_reader.py` 覆盖末行截断后续读、完整和两类不完整工具链；`tests/session/test_listing_cleanup.py` 覆盖记录时间排序、降级标题、无有效消息不进入恢复列表、有效记录与空/全损坏 JSONL 的活动时间判定、tool-results mtime 边界、29 天 23 小时保留、30 天 1 分删除、异步入口在线程执行及局部清理失败。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/session/test_writer.py tests/session/test_reader.py tests/session/test_listing_cleanup.py -q
```
预期：全部通过；只采用最后一笔完整提交的压缩事务，会话列表按有效记录时间排序，无有效记录会话仍有 30 天清理兜底，异步入口不阻塞事件循环且清理失败彼此隔离。

## T5：实现主动恢复 UI 与完整 SessionRuntime 原子切换

**文件：**
- Create: `src/novacode/tui/resume.py`
- Modify: `src/novacode/tui/commands.py`
- Modify: `src/novacode/tui/app.py`
- Modify: `src/novacode/agent/__init__.py`
- Test: `tests/session/test_reader.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_tui.py`

**依赖：** T4。

**步骤：**
1. 在 `commands.py` 只新增 `/resume` 注册；在 `app.py` 为 `SessionState` 新增 `RESUMING`。`resume.py` 将 `SessionInfo` 转为 Textual `OptionList` 项，支持选择和取消，不扩展通用命令解析器，也不自动恢复最近会话。
2. 恢复选择后先调用 `load_session()`，用 `Conversation.from_messages()` 建立无写盘钩子的 detached candidate；用 `open_session_context()` 保留原 ID，并准备包含全新 `ContentReplacementState`、`RecoveryState`、`CompactCircuitBreaker`、归零锚点和目标 session 的最终 `SessionRuntime`。
3. 给 `Agent.run_force_compact()` 增加可选 `runtime: SessionRuntime | None = None`。候选历史超出当前安全阈值时，创建 `<sessions_dir>/.resume-staging-<transaction_id>/tool-results/` 及一次性 staging `SessionRuntime`，显式把 staging runtime 传给现有 ch08 压缩入口，绝不让 detached candidate 使用最终目标 runtime。压缩失败显示可见错误、删除 staging、关闭新资源并保持当前活动会话不变。
4. 在 `app.py` 实现窄 helper：一个只负责在 staging runtime 压缩；另一个把 staging spill 迁入目标 `tool-results/`、为每个目标选择未占用名称、记录本次新文件清单并重写候选消息路径。迁移不得覆盖目标已有文件；部分失败只按清单删除本次新文件并删除 staging，不扫描或删除目标目录已有文件。
5. 候选压缩成功时，先完成 spill 迁移和路径重写，再为原 ID 打开 `SessionWriter.open_existing()`，把 detached candidate 的替换历史通过 `append_compaction()` 完整提交；commit 完成 `fsync` 后才创建绑定该 writer 钩子的 live `Conversation`。恢复历史本身不逐条回写；失败后 JSONL 中残留的未提交事务由 reader 忽略。
6. 根据最后活动时间设置最终目标 runtime 的 `resume_reminder`：严格超过 24 小时时生成仅用于当前请求的 system reminder，提醒重新读取易变资料；提醒不加入 `Conversation`、不写 JSONL、不伪装为 user。新会话该字段为空。
7. 以 `compact_commit` 完整写入并 `fsync` 为不可逆边界。commit 前失败保持旧活动会话和目标 JSONL 不变，删除 staging 并按清单删除本次目标新文件；commit 后 runtime 切换失败保持旧活动引用并报告未切换，但只删除 staging 源目录，保留已提交事务引用的全部目标迁移文件供下次 `/resume`。切换后旧 writer 关闭失败只记录且不回滚。
8. 测试 commit 前压缩/迁移/事务阶段失败与 commit 后切换失败：前者精确回收本次新文件，后者断言目标迁移文件保留且已提交事务可再次恢复；继续覆盖同名文件不覆盖、提醒边界、旧 append 排空和关闭失败。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/session/test_reader.py tests/test_agent.py tests/test_tui.py -q
```
预期：全部通过；恢复成功后所有新消息和迁移后的工具结果只进入被选中的原 session ID，任何切换前失败均不改变活动会话，staging 和本次部分迁移文件被清理且目标已有文件不受影响。

## T6：实现记忆类型、固定路由、独立文件与双限额索引存储

**文件：**
- Create: `src/novacode/memory/__init__.py`
- Create: `src/novacode/memory/types.py`
- Create: `src/novacode/memory/store.py`
- Create: `src/novacode/memory/prompts.py`
- Test: `tests/memory/test_store.py`

**依赖：** T5。

**步骤：**
1. 在 `types.py` 定义 `MemoryKind` 的 `user`、`feedback`、`project`、`reference`，以及 `MemoryAction(action, kind, memory_id, title, summary, content, filename)`、`MemoryTurn(user_content, assistant_content)`、`ApplyReport(created, updated, deleted, rejected)`；操作只允许 `create`、`update`、`delete`、`no-op`。
2. 实现 `MemoryStore(directory, allowed_kinds)`、进程内 `asyncio.Lock` 和独立 `.memory-write.lock`。跨进程锁使用标准库原子独占创建或等价互斥并记录 PID；活 PID 不抢占，死 PID或明确陈旧锁安全回收。两级 store 固定用户级→项目级获取、反向释放；该锁与 governor 的 `.consolidate-lock` 分工独立。
3. 每条记忆保存为独立 Markdown 文件，YAML frontmatter 至少包含稳定 UUID、`type`、`title`、`created`、`updated`；正文独立可理解，时间敏感信息使用绝对日期。每个目录的 `MEMORY.md` 只保存类型、标题、简述和可点击相对链接，不嵌入正文。
4. `read_index_locked()`、`render_index_locked()`、`apply_locked()` 仅允许持锁调用。`apply_locked()` 在内存中按 delete、update/merge、create 顺序逐项构造候选文件和候选索引；每项后同时检查不超过 200 行及 UTF-8 大小不超过 25KB。
5. 容量预演通过后，为全部 note/index 临时文件使用唯一 txn ID并逐个 `fsync`；再写入和 `fsync` `.memory-transaction.<txn>.tmp`，以 `os.replace()` 发布为 `.memory-transaction.json`，随后 `fsync` memory 目录。只有正式 journal 出现后才替换正文，index 仍最后提交。
6. `recover_locked()` 必须同时持有两种锁。无正式 journal 时只清理未被 index 引用的 txn 临时文件；有效 journal 正常 roll-forward。正式 journal 无法解析、校验失败或引用越界时标记 recovery-required，记录无正文错误，保留 journal/临时/正文并阻断后续提取与治理写入，不得猜测或静默删文件。
7. 在 `prompts.py` 定义提取和治理的结构化提示约束及响应解析入口，但不调用 provider；提示明确固定路由、四种操作、完整索引判断重复/冲突及禁止工具调用。
8. 测试 journal 发布前崩溃、发布后每个替换阶段、无正式 journal 孤儿清理、损坏/越界 journal 阻断写入且不丢文件；并覆盖 `.memory-write.lock` 活/死 PID、两级固定顺序及 200 行/25KB 语义。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/memory/test_store.py -q
```
预期：全部通过；任何时刻可提交的 `MEMORY.md` 都同时满足 200 行和 25KB 两项限制，任一替换阶段崩溃后 load 可幂等恢复且无悬空索引或永久未索引正文。

## T7：实现每轮自动提取的单消费者队列与完整锁区间

**文件：**
- Create: `src/novacode/memory/extractor.py`
- Modify: `src/novacode/memory/prompts.py`
- Modify: `src/novacode/memory/__init__.py`
- Test: `tests/memory/test_extractor.py`

**依赖：** T6。

**步骤：**
1. 实现 `MemoryExtractor(user_store, project_store, on_index_changed)`；构造只创建队列并保存 stores/callback，不接收 provider、不启动 worker。新增一次性 `bind_provider(provider: Provider) -> None`：拒绝空值和绑定到不同 provider 的第二次调用，并由 app 在成功绑定后启动唯一 `run()` 消费者；绑定前 `submit()` 拒绝任务。
2. 绑定后 `submit(MemoryTurn)` 只执行 `asyncio.Queue.put_nowait()`。每项任务固定按用户级→项目级获取各 store 的进程内锁和 `.memory-write.lock`，持双锁完成恢复、最新索引读取、provider 推理、校验和提交，最后反向释放；任何路径不得反序。
3. provider 请求仅包含最近一轮 user 与最终 assistant、两级完整索引、固定类型路由和四种操作约束，并显式设置 `Request.tools=[]`。是否保存、重复、冲突及 create/update/delete/no-op 由 LLM 基于最新索引判断，不增加 embedding 或相似度机制。
4. 逐项校验 action、kind、推导路由、filename 和规范化边界；`no-op` 不产生变更，非法操作只计入拒绝并记录无敏感正文诊断。
5. 模型错误、解析错误、容量拒绝或写入错误只使当前队列项失败；在 `finally` 释放全部锁和队列执行权，继续下一项。`close()` 先停止接收新项，并等待当前项到安全提交点或按可控方式取消，不能遗留 task、锁或可向下一会话写入的 provider 引用。
6. 在 `tests/memory/test_extractor.py` 测试构造后无 worker、绑定前 submit 拒绝、一次绑定只启动一个消费者、不同 provider 二次绑定拒绝；再测试三轮快速提交立即返回且严格串行，延迟第一项后第二项必须在第一项提交或失败收尾后读取最新索引，并覆盖空工具定义、固定双锁顺序、journal 恢复后读取、no-op、非法操作、provider/解析/写入失败继续下一项、索引刷新和排空/受控取消关闭。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/memory/test_extractor.py -q
```
预期：全部通过；绑定前没有 worker 或任务，同一会话只能绑定一个 provider 且最多一个提取推理在运行，后一项始终读取前一项完成后的最新索引。

## T8：实现记忆治理五门控、跨进程锁与失败恢复

**文件：**
- Create: `src/novacode/memory/governor.py`
- Modify: `src/novacode/memory/prompts.py`
- Modify: `src/novacode/memory/__init__.py`
- Test: `tests/memory/test_governor.py`

**依赖：** T7。

**步骤：**
1. 实现 `MemoryGovernor(sessions_dir, stores, run_restricted_agent, notify)`。`maybe_schedule(now)` 先读取旧 `_last_scan_at`；未满 10 分钟直接返回且不更新时间，通过后立即把本次实际扫描时间写为 `now`，即使后续门控失败也保留。
2. 依次检查至少一个 memory 目录存在、距 `.consolidate-lock` 最近成功 mtime 至少 24 小时、`list_sessions()` 至少返回 5 个可恢复会话、成功获取跨进程锁；任一失败立即返回且不创建后台任务。
3. `.consolidate-lock` 保存运行中 PID，mtime 表示最近一次成功治理。先用 OS 非阻塞文件锁保证并发最多一个持有者；PID 确认存活时不论锁龄都不抢占，PID 确认死亡时原子回收，仅在平台无法可靠判断 PID 时以超过 1 小时作为兜底，未知且未超时则保留。
4. 获锁后保存原 mtime。治理成功时清除运行态 PID、更新成功 mtime 并释放；失败、取消或异常时恢复原 mtime 后释放，使失败不会推迟下一次满足 24 小时门槛的治理。
5. `.consolidate-lock` 只控制治理调度；后台治理实际写入前仍按用户级→项目级获取每个 store 的进程内锁和 `.memory-write.lock`，反向释放。测试 extractor/extractor、extractor/governor 多进程竞争、死锁回收和固定顺序无死锁；变更仍经双限额与 journal 事务。
6. 治理负责合并重复、删除过时、修正确有证据的矛盾、把相对日期改为绝对日期并修剪索引。任务不阻塞启动、Agent Loop 或输入；结束后只通知状态与 create/update/delete 计数，不包含记忆正文。
7. `close()` 取消或收尾后台治理，并按失败语义恢复 mtime、释放 store 锁和跨进程锁。测试五道门控、扫描节流、活/死/未知 PID、1 小时边界、双检查竞争、成功更新时间、异常/取消恢复、受限能力、非阻塞与无正文通知。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/memory/test_governor.py -q
```
预期：全部通过；五道门控全部满足时才创建一个后台任务，失败或取消后锁 mtime 等于治理前值。

## T9：贯通 prompt、Agent、TUI 与 CLI 的五条数据流

**文件：**
- Modify: `src/novacode/prompt/__init__.py`
- Modify: `src/novacode/prompt/modules.py`
- Modify: `src/novacode/agent/__init__.py`
- Modify: `src/novacode/tui/app.py`
- Modify: `src/novacode/tui/commands.py`
- Modify: `src/novacode/cli.py`
- Test: `tests/test_conversation.py`
- Test: `tests/test_prompt.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_tui.py`
- Test: `tests/test_mcp_cli.py`

**依赖：** T8。

**步骤：**
1. 将 `optional_modules(instructions="", memory_index="")` 和 `build_system_prompt(instructions="", memory_index="")` 接到现有模块组装：`自定义指令` 保持 priority 80，`长期记忆` 保持 priority 100；空内容省略。记忆索引固定用户级在前、项目级在后，用清晰标题分隔，只注入两个 `MEMORY.md`，不注入独立正文。
2. 给 `Agent` 增加可选 `instructions: str` 与 `memory_index: Callable[[], str]`；每轮开始仍调用现有 `build_system_prompt()`，指令使用启动缓存，记忆 callable 返回最近一次成功提交后的合规索引。`SessionRuntime` 增加可选 `resume_reminder`，并通过现有 `Request.reminder` 以 system 语义注入。
3. `Event` 增加 `memory_turn: MemoryTurn | None`；只有 assistant 最终回复已成功持久化且无待执行 tool calls 时填充。`Conversation` 抛出的持久化异常转为 `Event.err`，停止本轮 provider 请求或后续内存写入，不吞掉错误。
4. 扩展 `NovaCodeApp` 注入 `project_root`、`session_context`、必选 writer、构造后未绑定的可选 extractor、可选 governor 和缓存索引。provider 选定后保持输入禁用，必须依次调用 `writer.bind_model(provider.model)`、`extractor.bind_provider(provider)` 并启动唯一消费者，成功创建 Agent 后才开放输入；任一步失败显示可处理错误、关闭本次已启动的 worker 并保持输入禁用，同一会话禁止重绑到不同 provider。
5. 消息追加流固定为：`Conversation.add_*()` 持有 conversation 锁，writer 持有自身锁完成 serialize → append → flush → fsync，再更新内存。用户消息失败时不渲染气泡、不启动 Agent；assistant/tool 失败时以可见错误收尾。
6. 最终事件消费顺序固定为：先渲染回复并恢复输入，再对非空 `memory_turn` 调用 `MemoryExtractor.submit()`；每个最终回复提交一次，不加轮数或关键词门槛，入队不得阻塞下一轮。
7. 在 `cli._amain()` 按唯一启动顺序装配：解析项目根和 sessions 目录 → 新建 session context → 加载并缓存四层指令 → 创建两级 store、锁内 roll-forward journal 并读取索引 → 初始化未绑定 model 的 writer → 组装 prompt/创建未绑定且无 worker 的 extractor → 注入 app → 后台调度 `clean_expired_async()`（内部 `asyncio.to_thread`）→ governor 懒检查 → 进入 TUI provider 选择。writer 初始化失败显示错误并返回非零；指令/索引缺失与后台失败按空值或日志降级。
8. 恢复流复用 T5：`/resume` 列表 → detached candidate → staging runtime → spill 迁移/路径重写 → 目标压缩事务提交 → 原子切换。staging 源目录始终清理；commit `fsync` 前失败才按清单删除本次目标新文件，commit `fsync` 后切换失败则保留目标文件、报告“已提交但未切换”并保持旧活动引用，供下次 `/resume` 使用。
9. 自动提取流复用 T7：最终回复持久化 → 渲染/恢复输入 → put_nowait → 固定双锁 → 最新索引 → 无工具推理 → 校验/预演/提交 → 刷新快照。治理流复用 T8，后台任务与主交互隔离。
10. 退出时先停止新输入、writer 提交和 extractor submit，关闭 extractor 队列并排空或受控取消唯一消费者，取消 governor 并恢复失败 mtime，等待或取消线程包装的 cleanup，清理恢复 staging，最后排空并关闭当前 writer；可删除从未成功持久化消息的当前临时会话，但所有无有效记录会话仍保留 30 天兜底，不得让旧项目或旧 session 的后台任务写入新目标。
11. 更新测试，覆盖两个 prompt 模块的空/非空与顺序、writer/extractor 顺序绑定、五条数据流、每轮提取、持久化错误、后台降级、主动恢复 staging、切换和退出无跨 session 写入；在现有 `tests/test_mcp_cli.py` 中覆盖 `_amain()` 的装配顺序、writer 初始化失败返回非零且不启动 TUI、任一绑定失败不开放输入，以及 cleanup 在线程包装中运行、cleanup/governor 异常只记录并继续其余初始化。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation.py tests/test_prompt.py tests/test_agent.py tests/test_tui.py tests/test_mcp_cli.py -q
```
预期：全部通过；启动加载、消息追加、恢复会话、自动提取、记忆治理五条数据流均按规定顺序执行，失败边界不污染内存或活动会话。

## T10：完成自动化回归与 tmux 真实对话验收

**文件：**
- Test: `tests/instructions/test_loader.py`
- Test: `tests/session/test_writer.py`
- Test: `tests/session/test_reader.py`
- Test: `tests/session/test_listing_cleanup.py`
- Test: `tests/memory/test_store.py`
- Test: `tests/memory/test_extractor.py`
- Test: `tests/memory/test_governor.py`
- Test: `tests/test_conversation.py`
- Test: `tests/test_prompt.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_tui.py`
- Test: `tests/test_mcp_cli.py`
- Verify: `docs/ch09/项目记忆与会话持久化 Spec.md`（AC1–AC27 权威验收依据）
- Reference: `docs/ch09/项目记忆与会话持久化 Checklist.md`（本次文档更新流程生成的镜像执行清单）

**依赖：** T9。

**步骤：**
1. 在任何 ch09 实现改动前，使用下方 Windows 全量 pytest、ruff check、ruff format、compileall 和 `git diff --check` 命令记录基线，保存命令、退出码和关键输出。以已批准 Spec 的 AC1–AC27 为唯一权威验收依据，建立“AC 编号 → 自动化测试或 tmux 场景 → 证据”的逐项映射；即使 Checklist 缺失，也必须能直接依据 Spec 完成自动化验收。
2. 实施前检查 `docs/ch09/项目记忆与会话持久化 Checklist.md` 是否逐项镜像 AC1–AC27 的编号和语义；Checklist 有缺失或冲突时以 Spec 为准并先修正文档，在一致的 Checklist 可用前不开始 tmux 场景。
3. 逐个运行四个新增模块测试。自动化测试必须确定性覆盖：指令精确展开边界；会话坏行、工具链和所有压缩/staging 崩溃点；有效/无效记录清理及线程包装；四类记忆精确路由；MemoryStore journal 每个替换阶段；extractor 延迟绑定、提取延迟和串行化；治理门控、异常、锁竞争和失败恢复。上述场景不得依赖真实 provider 或 tmux 中模型碰巧输出指定结构化操作。
4. 运行 Conversation、prompt、Agent、TUI 与 CLI 集成测试，确认 writer → extractor 的 provider 绑定顺序、可选能力为空时保持既有行为、持久化失败可见且不造成内存领先于磁盘、恢复 staging 精确清理、异步 cleanup 不阻塞、切换/退出排空以及后台降级符合契约。
5. 实现后重新运行与基线相同的全量命令。只有能通过最小复现、提交范围或前后对比证明由 ch09 引入的失败才在本任务内修复；既有或无关失败必须记录命令、退出码、关键输出和归因，作为阻塞证据请求范围扩展，不得擅自修改 ch09 无关代码，也不得把未通过记录成通过。
6. tmux 验收只能在 Linux/WSL、仓库 Linux `.venv`、tmux 和真实 provider 配置均可用，且 Checklist 与 AC1–AC27 一致时执行。先保存 `REAL_HOME` 与 `REPO`，创建临时根、临时 HOME、临时 workspace 和独立 tmux socket；只把 provider 启动必需配置复制到临时 HOME 的 `.novacode/` 并设权限 600，使用 trap/finally 在退出时关闭独立 tmux server 并删除临时目录/socket。不得修改真实 `~/.novacode/`、真实项目指令/记忆或仓库工作树；当前环境缺少任一前置时记录“未执行”及原因。
7. 通过 `PYTHONPATH="$REPO/src"` 从临时 workspace 启动 NovaCode。tmux 只采集真实 provider 可稳定观察的烟雾/生命周期证据：冷启动；临时 HOME/workspace 中四层指令的基本优先级；一个合法独占行引用；一次真实工具调用；最终回复后继续输入；`/exit` 返回持久 shell；重启 `/resume` 后原 ID 续写。不要要求模型确定地产生四类记忆、固定提取延迟、治理操作或故障结果。
8. 在隔离 workspace 准备超过 24 小时的恢复样本，确认只向当前上下文注入 system reminder；用自动化测试先构造并验证已提交压缩事务，再在 tmux 中恢复该已提交样本并确认原 ID 续写。未提交事务、30 天空会话清理、staging/journal 崩溃注入和治理异常继续以自动化测试为权威证据，不在真实 provider 会话中破坏性注入。
9. 按 AC1–AC27 映射记录自动化与 tmux 证据；Checklist 只作为镜像操作清单。正常 `/exit` 是 writer/extractor 收尾证据，最终关闭独立 tmux server 仅用于环境清理，不作为 AC26 证据。trap/finally 结束后确认临时目录和 socket 已删除，真实 HOME 与仓库 `git status` 未因 tmux 验收发生变化。

**验证：**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/instructions/test_loader.py -q
.\.venv\Scripts\python.exe -m pytest tests/session/test_writer.py tests/session/test_reader.py tests/session/test_listing_cleanup.py -q
.\.venv\Scripts\python.exe -m pytest tests/memory/test_store.py tests/memory/test_extractor.py tests/memory/test_governor.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_conversation.py tests/test_prompt.py tests/test_agent.py tests/test_tui.py tests/test_mcp_cli.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\python.exe -m compileall src
git diff --check
```
预期：记录实施前基线并完成实施后对比；ch09 引入的回归全部修复。若存在既有或无关失败，保留命令、退出码、关键输出和归因并请求范围扩展，不修改无关代码；`git diff --check` 无输出，且 AC1–AC27 均有自动化或待执行的隔离 tmux 证据映射。

满足 tmux 前置条件后，在 Linux/WSL 中运行：
```bash
REAL_HOME="$HOME"
REPO="$PWD"
TMP_ROOT="$(mktemp -d)"
export HOME="$TMP_ROOT/home"
WORKSPACE="$TMP_ROOT/workspace"
TMUX_SOCKET="$TMP_ROOT/tmux.sock"
mkdir -p "$HOME/.novacode" "$WORKSPACE"
install -m 600 "$REAL_HOME/.novacode/config.yaml" "$HOME/.novacode/config.yaml"
cleanup_ch09_e2e() {
  tmux -S "$TMUX_SOCKET" kill-server 2>/dev/null || true
  rm -rf "$TMP_ROOT"
}
trap cleanup_ch09_e2e EXIT
tmux -S "$TMUX_SOCKET" new-session -d -s novacode-ch09 -c "$WORKSPACE"
tmux -S "$TMUX_SOCKET" send-keys -t novacode-ch09:0.0 "HOME='$HOME' PYTHONPATH='$REPO/src' '$REPO/.venv/bin/python' -m novacode" Enter
tmux -S "$TMUX_SOCKET" attach-session -t novacode-ch09
```
预期：真实 provider 只验证冷启动、四层指令基本优先级、合法引用、真实工具调用、最终回复后继续输入、`/exit`、`/resume`、24 小时样本和已提交压缩恢复；`/exit` 后持久 shell 仍存在。四类精确路由、提取延迟、治理门控/异常和崩溃注入以自动化测试为准。全部证据记录后由 trap/finally 清理独立 socket 与临时 HOME/workspace，真实 `~/.novacode/` 和仓库工作树不发生变化；关闭 tmux server 不作为 AC26 正常退出或资源排空证据。若 Linux/WSL、tmux、真实 provider 或一致 Checklist 任一不可用，记录“未执行”及缺失条件，不得记录为通过。
