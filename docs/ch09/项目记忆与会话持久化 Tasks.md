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

**依赖：** 无。

**步骤：**
1. 将 `SessionContext` 固定为 `session_id`、`message_path`、`spill_dir` 三个字段；`message_path` 指向 `<workspace>/.novacode/sessions/<session_id>.jsonl`，`spill_dir` 指向同 ID 目录下的 `tool-results/`，两者不能互相嵌套或改用不同 ID。
2. 实现 `new_session_id(now: datetime | None = None) -> str`：使用本地时间生成 `YYYYMMDD-HHMMSS`，追加 `secrets.token_hex(2)` 产生的 4 位小写十六进制后缀；对注入的 `now` 保持确定性时间部分，随机后缀仍校验格式。
3. 修改 `new_session_context(workspace: str) -> SessionContext`：创建 sessions 根目录与 `spill_dir`，返回同一 session ID 对应的消息路径；不预写消息记录。
4. 新增 `open_session_context(workspace: str, session_id: str) -> SessionContext`：先用完整正则校验 ID，再要求消息文件已经存在；只打开原 ID 的关联路径，不重命名、不迁移、不创建替代消息文件。
5. 在 `tests/compact/test_state.py` 覆盖格式、同 ID 双路径、工具目录创建、非法 ID、消息文件缺失和恢复原路径；保留 ch08 只消费 `spill_dir` 的既有测试。

**验证：**
```powershell
pytest tests/compact/test_state.py -q
```
预期：全部通过；生成 ID 匹配 `^\d{8}-\d{6}-[0-9a-f]{4}$`，消息文件路径和工具结果目录共享同一 ID。

## T2：建立 SessionWriter 与 Conversation 的先盘后内存提交点

**文件：**
- Create: `src/novacode/session/__init__.py`
- Create: `src/novacode/session/types.py`
- Create: `src/novacode/session/codec.py`
- Create: `src/novacode/session/writer.py`
- Modify: `src/novacode/conversation.py`
- Test: `tests/test_session.py`
- Test: `tests/test_conversation.py`

**依赖：** T1。

**步骤：**
1. 在 `types.py` 定义 `SessionWriteError(OSError)`；在 `codec.py` 集中实现 `Message` 与普通 JSONL 记录的无状态编解码，记录至少包含 `type="message"`、`role`、UTC ISO 8601 `ts`，并按消息形态保留 `content`、完整 tool call 或完整 tool result，其中首条消息额外写入已绑定 `model`。
2. 实现 `SessionWriter(sessions_dir: Path, session_id: str, model: str)`，其 `path` 必须为 `<sessions_dir>/<session_id>.jsonl`。构造器只以追加文本模式打开该文件；允许新会话以空 model 初始化，但此状态下 `append_message()` 必须抛 `SessionWriteError`。
3. 实现 `bind_model(model: str)`：在 writer 的 `threading.RLock` 内拒绝空值、首条记录之后的首次绑定及与已绑定值不一致的重复绑定。实现 `open_existing()`，使用恢复结果首条记录中的 model 建立已绑定 writer。
4. 实现 `append_message()`：持有同一个 writer 锁完成序列化、写入一整行、`flush`、`os.fsync`；任一步失败都统一抛 `SessionWriteError`。实现 `close()`：禁止后续追加，等待已进入锁区的追加完成后关闭句柄，多次关闭保持幂等。
5. 给 `Conversation` 增加 `before_append: Callable[[Message], None] | None` 和 `before_replace: Callable[[list[Message]], None] | None`。所有 `add_*()` 在 `Conversation` 的 `RLock` 内先构造完整消息、调用 `before_append`，成功后才深拷贝进内存；`replace_history()` 同样先调用 `before_replace`，成功后才替换。
6. 实现 `Conversation.from_messages()`：深拷贝装入已校验历史并绑定可选钩子，初始装入不得触发写盘。默认无钩子时保持当前行为。
7. 测试 user、assistant、tool 消息字段与顺序、首条 model、未绑定拒绝、bind 冲突、flush/fsync、并发串行、关闭语义，以及序列化/写入/flush/fsync 失败均不改变 `Conversation`；另测 `from_messages()` 不回写旧历史。

**验证：**
```powershell
pytest tests/test_conversation.py tests/test_session.py -q
```
预期：全部通过；失败注入下磁盘与内存都不接受该条消息，成功路径中磁盘提交先于内存变更。

## T3：实现四层项目指令加载与安全引用展开

**文件：**
- Create: `src/novacode/instructions/__init__.py`
- Create: `src/novacode/instructions/loader.py`
- Test: `tests/test_instructions.py`

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
pytest tests/test_instructions.py -q
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
- Test: `tests/test_session.py`

**依赖：** T3。

**步骤：**
1. 在 `types.py` 定义不可变 `SessionInfo(session_id, title, model, last_activity, file_size, path)` 和 `SessionLoadResult(session_id, messages, model, last_activity, diagnostics)`；从 `session.__init__` 导出 writer、reader、listing、cleanup 的公开接口。
2. 在 `codec.py` 增加规范 JSON 序列化与 SHA-256 稳定摘要。`SessionWriter.append_compaction(replacement)` 使用唯一事务 ID，并在同一 writer 锁内依次追加 `compact_begin`、带连续序号的全部 `compact_message`、`compact_commit`；每行都执行 write/flush/fsync，只有 commit 完成 fsync 后才返回成功。
3. `load_session(path)` 从头逐行解析；坏 JSON、未知类型或字段非法只追加诊断并继续后续有效行。压缩事务只有在 begin、连续完整替换消息和 commit 的事务 ID、数量、序号、摘要全部一致时才替换事务前历史；未提交、损坏或不一致事务整体忽略。
4. 在压缩状态解释完成后严格校验工具链：assistant 的 tool calls 只能由紧随其后的 tool message 按相同 ID 和顺序完整返回；未闭合、部分返回或错序时从发起链的 assistant 之前截断，孤立 tool result 从其自身之前截断，同时保留此前最后完整边界。
5. `list_sessions(sessions_dir)` 只扫描 `*.jsonl`，以最后一条有效记录的活动时间倒序排序；无有效消息的文件不返回。标题取首条有效 user 内容的单行截断摘要，没有有效 user 时固定为 `（无用户消息）`；同时返回首条 model、有效活动时间和文件大小，不读取工具结果正文。
6. `clean_expired(sessions_dir, now, max_age=timedelta(days=30))` 复用相同有效活动时间，仅删除严格超过 30 天的 JSONL 及同 ID 工具结果目录；单项删除失败记录后继续，不阻塞调用方。
7. 扩充 `tests/test_session.py`：覆盖普通往返、末行截断后续读、压缩三阶段成功、begin 后中断、部分替换中断、摘要/序号不一致、完整和两类不完整工具链、记录时间排序、降级标题、无有效消息跳过、29 天 23 小时保留、30 天 1 分删除及局部清理失败。

**验证：**
```powershell
pytest tests/test_session.py -q
```
预期：全部通过；只采用最后一笔完整提交的压缩事务，会话列表按有效记录时间排序，清理失败彼此隔离。

## T5：实现主动恢复 UI 与完整 SessionRuntime 原子切换

**文件：**
- Create: `src/novacode/tui/resume.py`
- Modify: `src/novacode/tui/commands.py`
- Modify: `src/novacode/tui/app.py`
- Modify: `src/novacode/agent/__init__.py`
- Test: `tests/test_session.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_tui.py`

**依赖：** T4。

**步骤：**
1. 在 `commands.py` 只新增 `/resume` 注册；在 `app.py` 为 `SessionState` 新增 `RESUMING`。`resume.py` 将 `SessionInfo` 转为 Textual `OptionList` 项，支持选择和取消，不扩展通用命令解析器，也不自动恢复最近会话。
2. 恢复选择后先调用 `load_session()`，用 `Conversation.from_messages()` 建立无写盘钩子的 detached candidate；用 `open_session_context()` 保留原 ID，并创建包含全新 `ContentReplacementState`、`RecoveryState`、`CompactCircuitBreaker`、归零锚点和目标 session 的完整 `SessionRuntime`。
3. 给 `Agent.run_force_compact()` 增加可选 `runtime: SessionRuntime | None = None`。候选历史超出当前安全阈值时，显式把目标 runtime 传给现有 ch08 压缩入口；压缩失败显示可见错误、关闭新资源并保持当前会话和目标存档不变。
4. 候选压缩成功时，先为原 ID 打开 `SessionWriter.open_existing()`，把 detached candidate 的替换历史通过 `append_compaction()` 完整提交，再创建绑定该 writer 钩子的 live `Conversation`。恢复历史本身不逐条回写。
5. 根据最后活动时间设置目标 runtime 的 `resume_reminder`：严格超过 24 小时时生成仅用于当前请求的 system reminder，提醒重新读取易变资料；提醒不加入 `Conversation`、不写 JSONL、不伪装为 user。新会话该字段为空。
6. 切换前完整准备并验证新 writer、目标 runtime 和 live conversation。进入短临界区后禁止新消息提交，原子替换 `conv`、writer 与整个 runtime 引用，再恢复提交；退出临界区后等待旧 writer 已进入的 append 完成并关闭。切换前失败保持旧引用，切换后旧 writer 关闭失败只记录且不回滚。
7. 测试列表选择/取消、原 ID 续写、目标工具目录、23 小时 59 分无提醒、24 小时 1 分有临时提醒、恢复超阈值使用目标 runtime、压缩失败不切换、准备失败清理新资源、切换中禁止提交、旧 append 排空和旧 writer 关闭失败不回切。

**验证：**
```powershell
pytest tests/test_session.py tests/test_agent.py tests/test_tui.py -q
```
预期：全部通过；恢复成功后所有新消息和工具结果只进入被选中的原 session ID，任何切换前失败均不改变活动会话。

## T6：实现记忆类型、固定路由、独立文件与双限额索引存储

**文件：**
- Create: `src/novacode/memory/__init__.py`
- Create: `src/novacode/memory/types.py`
- Create: `src/novacode/memory/store.py`
- Create: `src/novacode/memory/prompts.py`
- Test: `tests/test_memory.py`

**依赖：** T5。

**步骤：**
1. 在 `types.py` 定义 `MemoryKind` 的 `user`、`feedback`、`project`、`reference`，以及 `MemoryAction(action, kind, memory_id, title, summary, content, filename)`、`MemoryTurn(user_content, assistant_content)`、`ApplyReport(created, updated, deleted, rejected)`；操作只允许 `create`、`update`、`delete`、`no-op`。
2. 实现 `MemoryStore(directory, allowed_kinds)` 及其 `asyncio.Lock`。用户级 store 只允许 `user/feedback`，项目级 store 只允许 `project/reference`；目标目录由 kind 推导，拒绝模型指定的层级、非法文件名、路径越界、类型错路由和缺失目标。
3. 每条记忆保存为独立 Markdown 文件，YAML frontmatter 至少包含稳定 UUID、`type`、`title`、`created`、`updated`；正文独立可理解，时间敏感信息使用绝对日期。每个目录的 `MEMORY.md` 只保存类型、标题、简述和可点击相对链接，不嵌入正文。
4. `read_index_locked()`、`render_index_locked()`、`apply_locked()` 仅允许持锁调用。`apply_locked()` 在内存中按 delete、update/merge、create 顺序逐项构造候选文件和候选索引；每项后同时检查不超过 200 行及 UTF-8 大小不超过 25KB。
5. 任一操作导致候选超限时，只拒绝该操作并恢复到该操作前候选状态，记录结构化诊断；不得短暂落盘超限索引，也不得启动治理兜底。合法变更通过同目录临时文件和 `os.replace()` 原子提交，并在成功后返回准确计数。
6. 在 `prompts.py` 定义提取和治理的结构化提示约束及响应解析入口，但不调用 provider；提示明确固定路由、四种操作、完整索引判断重复/冲突及禁止工具调用。
7. 测试四类路由、frontmatter、相对链接、四种操作、非法层级/文件名/越界、稳定 created 与更新后的 updated、原子失败回滚、第 201 行、超过 25KB、先 delete/update 释放容量后 create 成功，以及索引不含正文。

**验证：**
```powershell
pytest tests/test_memory.py -q
```
预期：全部通过；任何时刻磁盘上的 `MEMORY.md` 都同时满足 200 行和 25KB 两项限制。

## T7：实现每轮自动提取的单消费者队列与完整锁区间

**文件：**
- Create: `src/novacode/memory/extractor.py`
- Modify: `src/novacode/memory/prompts.py`
- Modify: `src/novacode/memory/__init__.py`
- Test: `tests/test_memory.py`

**依赖：** T6。

**步骤：**
1. 实现 `MemoryExtractor(provider, user_store, project_store, on_index_changed)`；`submit(MemoryTurn)` 只执行 `asyncio.Queue.put_nowait()`，不等待 provider、不修改会话历史，队列只由一个 `run()` 消费者按提交顺序处理。
2. 每项任务固定按用户级、项目级顺序获取两个 store 锁；持锁后重新读取最新两级完整索引，并在持锁期间完成 provider 推理、结构化解析、操作校验、双限额预演和原子提交，最后刷新 prompt 使用的两级索引快照。
3. provider 请求仅包含最近一轮 user 与最终 assistant、两级完整索引、固定类型路由和四种操作约束，并显式设置 `Request.tools=[]`。是否保存、重复、冲突及 create/update/delete/no-op 由 LLM 基于最新索引判断，不增加 embedding 或相似度机制。
4. 逐项校验 action、kind、推导路由、filename 和规范化边界；`no-op` 不产生变更，非法操作只计入拒绝并记录无敏感正文诊断。
5. 模型错误、解析错误、容量拒绝或写入错误只使当前队列项失败；在 `finally` 释放全部锁和队列执行权，继续下一项。`close()` 停止接收新项，并等待当前项到安全提交点或按可控方式取消，不能遗留锁。
6. 测试三轮快速提交时立即返回且严格串行；延迟第一项后，第二项必须在第一项提交或失败收尾后读取最新索引；再覆盖空工具定义、固定双锁顺序、no-op、非法操作、provider/解析/写入失败继续下一项、索引刷新和安全关闭。

**验证：**
```powershell
pytest tests/test_memory.py -q
```
预期：全部通过；同一项目最多一个提取推理在运行，后一项始终读取前一项完成后的最新索引。

## T8：实现记忆治理五门控、跨进程锁与失败恢复

**文件：**
- Create: `src/novacode/memory/governor.py`
- Modify: `src/novacode/memory/prompts.py`
- Modify: `src/novacode/memory/__init__.py`
- Test: `tests/test_memory_governor.py`

**依赖：** T7。

**步骤：**
1. 实现 `MemoryGovernor(sessions_dir, stores, run_restricted_agent, notify)`。`maybe_schedule(now)` 先读取旧 `_last_scan_at`；未满 10 分钟直接返回且不更新时间，通过后立即把本次实际扫描时间写为 `now`，即使后续门控失败也保留。
2. 依次检查至少一个 memory 目录存在、距 `.consolidate-lock` 最近成功 mtime 至少 24 小时、`list_sessions()` 至少返回 5 个可恢复会话、成功获取跨进程锁；任一失败立即返回且不创建后台任务。
3. `.consolidate-lock` 保存运行中 PID，mtime 表示最近一次成功治理。先用 OS 非阻塞文件锁保证并发最多一个持有者；PID 确认存活时不论锁龄都不抢占，PID 确认死亡时原子回收，仅在平台无法可靠判断 PID 时以超过 1 小时作为兜底，未知且未超时则保留。
4. 获锁后保存原 mtime。治理成功时清除运行态 PID、更新成功 mtime 并释放；失败、取消或异常时恢复原 mtime 后释放，使失败不会推迟下一次满足 24 小时门槛的治理。
5. 后台任务按固定顺序获取目标 `MemoryStore.lock`，调用受限子 Agent：只暴露会话 JSONL 与两级 memory 的读取能力、目标 memory 目录的受约束写入能力；不注册 shell，不允许源码、边界外路径或未经现有资料支持的新事实。变更仍经 `MemoryStore` 校验和原子提交。
6. 治理负责合并重复、删除过时、修正确有证据的矛盾、把相对日期改为绝对日期并修剪索引。任务不阻塞启动、Agent Loop 或输入；结束后只通知状态与 create/update/delete 计数，不包含记忆正文。
7. `close()` 取消或收尾后台治理，并按失败语义恢复 mtime、释放 store 锁和跨进程锁。测试五道门控、扫描节流、活/死/未知 PID、1 小时边界、双检查竞争、成功更新时间、异常/取消恢复、受限能力、非阻塞与无正文通知。

**验证：**
```powershell
pytest tests/test_memory_governor.py -q
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

**依赖：** T8。

**步骤：**
1. 将 `optional_modules(instructions="", memory_index="")` 和 `build_system_prompt(instructions="", memory_index="")` 接到现有模块组装：`自定义指令` 保持 priority 80，`长期记忆` 保持 priority 100；空内容省略。记忆索引固定用户级在前、项目级在后，用清晰标题分隔，只注入两个 `MEMORY.md`，不注入独立正文。
2. 给 `Agent` 增加可选 `instructions: str` 与 `memory_index: Callable[[], str]`；每轮开始仍调用现有 `build_system_prompt()`，指令使用启动缓存，记忆 callable 返回最近一次成功提交后的合规索引。`SessionRuntime` 增加可选 `resume_reminder`，并通过现有 `Request.reminder` 以 system 语义注入。
3. `Event` 增加 `memory_turn: MemoryTurn | None`；只有 assistant 最终回复已成功持久化且无待执行 tool calls 时填充。`Conversation` 抛出的持久化异常转为 `Event.err`，停止本轮 provider 请求或后续内存写入，不吞掉错误。
4. 扩展 `NovaCodeApp` 注入 `project_root`、`session_context`、必选 writer、可选 extractor/governor 和缓存索引。provider 选定后必须先原子调用 `writer.bind_model(provider.model)`，成功才创建 Agent 并开放输入；绑定失败显示可处理错误并保持输入禁用。
5. 消息追加流固定为：`Conversation.add_*()` 持有 conversation 锁，writer 持有自身锁完成 serialize → append → flush → fsync，再更新内存。用户消息失败时不渲染气泡、不启动 Agent；assistant/tool 失败时以可见错误收尾。
6. 最终事件消费顺序固定为：先渲染回复并恢复输入，再对非空 `memory_turn` 调用 `MemoryExtractor.submit()`；每个最终回复提交一次，不加轮数或关键词门槛，入队不得阻塞下一轮。
7. 在 `cli._amain()` 按唯一启动顺序装配：解析项目根和 sessions 目录 → 新建 session context → 加载并缓存四层指令 → 创建两级 store 并读取索引 → 初始化未绑定 model 的 writer → 组装 prompt/创建 extractor → 注入 app → 后台调度 30 天清理 → governor 懒检查 → 进入 TUI。writer 初始化失败显示错误并返回非零；指令/索引缺失与后台失败按空值或日志降级。
8. 恢复流复用 T5：`/resume` 列表 → 读取并截断到完整边界 → detached candidate → 全新目标 runtime → 必要时在目标 runtime 压缩 → 压缩事务提交 → live conversation → 原子切换 → 关闭旧 writer；临时新会话存档不自动删除。
9. 自动提取流复用 T7：最终回复持久化 → 渲染/恢复输入 → put_nowait → 固定双锁 → 最新索引 → 无工具推理 → 校验/预演/提交 → 刷新快照。治理流复用 T8，后台任务与主交互隔离。
10. 退出时先停止新输入和提交，关闭 extractor 队列并等待安全点，取消 governor 并恢复失败 mtime，等待或取消 cleanup，最后排空并关闭当前 writer；不得让旧项目或旧 session 的后台任务写入新目标。
11. 更新测试，覆盖两个 prompt 模块的空/非空与顺序、首条 model 绑定、五条数据流、每轮提取、持久化错误、后台降级、主动恢复、切换和退出无跨 session 写入。

**验证：**
```powershell
pytest tests/test_conversation.py tests/test_prompt.py tests/test_agent.py tests/test_tui.py -q
```
预期：全部通过；启动加载、消息追加、恢复会话、自动提取、记忆治理五条数据流均按规定顺序执行，失败边界不污染内存或活动会话。

## T10：完成自动化回归与 tmux 真实对话验收

**文件：**
- Test: `tests/test_instructions.py`
- Test: `tests/test_session.py`
- Test: `tests/test_memory.py`
- Test: `tests/test_memory_governor.py`
- Test: `tests/test_conversation.py`
- Test: `tests/test_prompt.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_tui.py`
- Verify: `docs/ch09/项目记忆与会话持久化 Checklist.md`

**依赖：** T9。

**步骤：**
1. 逐个运行四个新增模块测试，确认指令、会话、记忆存储/提取和治理边界均可在无真实 provider、无 TUI 条件下验证。
2. 运行 Conversation、prompt、Agent 与 TUI 集成测试，确认可选能力为空时保持既有行为，持久化失败可见且不造成磁盘/内存分叉。
3. 运行全量 pytest、ruff check、ruff format 检查、compileall 和 `git diff --check`；任何失败都必须定位到具体任务并修复后重跑，不能以“与本章无关”代替通过。
4. 在配置好真实 provider 的环境中启动独立 tmux 会话，运行 `uv run nova`，输入真实请求：让 NovaCode 读取一个项目文件、调用至少一个工具并给出最终回复；观察每条 user/assistant/tool 记录已写入当前 JSONL，最终回复显示后输入立即可用，记忆提取在后台执行。
5. 在 tmux 中触发足以走 ch08 压缩路径的多轮对话，退出后重新启动并执行 `/resume`；选择刚才会话，确认完整工具链恢复、原 ID 续写、压缩事务可恢复且没有消息写入临时会话。
6. 准备超过 24 小时的恢复样本并确认仅当前上下文收到 system reminder；准备超过 30 天的存档并确认后台清理 JSONL 和同 ID 工具结果目录，同时交互仍可继续。
7. 对照 `docs/ch09/项目记忆与会话持久化 Checklist.md` 逐项记录证据；重点核对四层指令、五条数据流、四类记忆路由、索引双限额、单消费者顺序、治理五门控、锁失败恢复以及切换/退出生命周期。

**验证：**
```powershell
pytest tests/test_instructions.py -q
pytest tests/test_session.py -q
pytest tests/test_memory.py -q
pytest tests/test_memory_governor.py -q
pytest tests/test_conversation.py tests/test_prompt.py tests/test_agent.py tests/test_tui.py -q
pytest -q
ruff check .
ruff format --check .
python -m compileall -q src
git diff --check
tmux new-session -d -s novacode-ch09 "uv run nova"
tmux attach-session -t novacode-ch09
```
预期：所有自动化命令退出码为 0，`git diff --check` 无输出；tmux 中真实对话、工具调用、压缩、退出和 `/resume` 均通过 Checklist，最终可用 `tmux kill-session -t novacode-ch09` 结束验收会话。
