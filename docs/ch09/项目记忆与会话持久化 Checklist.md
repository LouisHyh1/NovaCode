# 项目记忆与会话持久化 Checklist

> 本清单是 `项目记忆与会话持久化 Spec.md` 中 AC1–AC27 的执行镜像。Spec 是唯一权威验收依据；若本清单与 Spec 冲突，以 Spec 为准并先修正本清单。每项必须记录实际命令、退出码、关键输出或文件证据；未执行的项目保持未勾选，并写明原因。

## 静态质量

- [ ] 在任何 ch09 实现改动前先运行本节全部 Windows 自动化命令并保存基线命令、退出码和关键输出；实现后用相同命令复跑并对比。只修复能由最小复现、提交范围或前后对比证明为 ch09 引入的回归；既有或无关失败记录命令、输出和归因，作为阻塞证据请求扩大范围，不擅自修改 ch09 无关代码，也不把未通过记录为通过。（AC27）
- [ ] 在 Windows 项目根运行 `.\.venv\Scripts\python.exe -m pytest tests/instructions/test_loader.py -q`、`.\.venv\Scripts\python.exe -m pytest tests/session/test_writer.py tests/session/test_reader.py tests/session/test_listing_cleanup.py -q`、`.\.venv\Scripts\python.exe -m pytest tests/memory/test_store.py tests/memory/test_tool.py tests/memory/test_extractor.py tests/memory/test_governor.py -q`，预期三组新增模块测试均退出码为 0，并分别覆盖指令、会话、显式记忆工具、隐式提取与治理边界。（AC1–AC23）
- [ ] 在 Windows 项目根运行 `.\.venv\Scripts\python.exe -m pytest tests/test_conversation.py tests/test_prompt.py tests/test_agent.py tests/test_tui.py tests/test_mcp_cli.py -q`，预期集成测试退出码为 0，且空配置兼容、持久化失败、主动恢复、后台降级和生命周期断言均通过。（AC24–AC27）
- [ ] 在 Windows 项目根运行 `.\.venv\Scripts\python.exe -m pytest -q`、`.\.venv\Scripts\ruff.exe check .`、`.\.venv\Scripts\ruff.exe format --check .`、`.\.venv\Scripts\python.exe -m compileall src` 和 `git diff --check`，预期所有命令退出码为 0、无测试失败、无 lint/格式/编译错误且无空白错误；同时检查依赖清单和文档未引入数据库、向量检索、embedding、RAG 记忆检索、完整 Slash Command 框架或第二套压缩格式。（AC27）

## 项目指令

- [ ] 在 `~/.novacode/NOVACODE.md`、项目根 `NOVACODE.md`、项目根 `.novacode/NOVACODE.md`、项目根 `NOVACODE.local.md` 分别写入可区分内容并启动 NovaCode，检查 `自定义指令` 按用户级、项目根、项目私有目录、本地覆盖的顺序出现，非空层之间只有独占行 `---`，且最后的 `NOVACODE.local.md` 具有最高优先级。（AC1）
- [ ] 仅保留四层中的任意一个 `NOVACODE.md` 后启动，再分别验证空文件和不可读引用；预期 NovaCode 正常启动，模块只包含可读取的非空内容，局部失败有诊断但不阻断其他内容加载。（AC2）
- [ ] 在指令文件中分别放置独占行 `@rules/style.md` 与段落内同名文本，并构造 6 层引用链；预期独占行被目标内容替换、段落内文本原样保留，只展开前 5 层引用并记录深度诊断。（AC3）
- [ ] 构造 A 引用 B、B 引用 A 的环路，以及两个独立分支引用同一文件的复用场景；预期当前访问链中的第二次 A 被跳过并诊断，而共享文件在两个独立分支中都被展开。（AC4）
- [ ] 分别用 `..`、越界符号链接和含空字节文件作为引用目标；预期规范化真实路径检查拒绝越过用户级 `.novacode/` 或项目根的目标，二进制内容不进入 prompt，三类拒绝均留下不含正文的诊断。（AC5）

## 会话写入

- [ ] 冷启动并检查新 session ID、消息文件与工具结果目录；预期 ID 匹配 `YYYYMMDD-HHMMSS-xxxx`，消息写入 `.novacode/sessions/<session_id>.jsonl`，工具结果写入 `.novacode/sessions/<session_id>/tool-results/`，两条路径共享同一 ID 且消息文件不位于工具结果目录内。（AC6）
- [ ] 用可观察 mock writer 依次提交 user、assistant 和 tool 消息，并分别注入序列化、append、flush、fsync 失败；预期成功路径中每条完整 JSON 行都先 append、flush、fsync 再更新 `Conversation`，失败消息不进入内存且调用方收到可处理错误。（AC7）
- [ ] 连续追加多条消息，确认既有字节未被重写；再截断最后一行并重启恢复，预期此前完整行仍可读取、坏行被跳过，随后继续向同一 JSONL 文件追加。（AC8）
- [ ] 分别准备完整工具链、缺少结果、多工具部分返回、ID/顺序不匹配和孤立 tool result；预期完整链全部保留，前三类不完整链从发起该链的 assistant 消息之前截断，孤立结果从其自身之前截断，并保留此前最后一个完整工具边界。（AC9）
- [ ] 以 `compact_commit` 完整写入并 fsync 为不可逆边界：commit 前各阶段失败时旧活动会话和目标 JSONL 不变，删除 staging 并按清单删除目标 tool-results 中本次新文件；commit 后 runtime 切换失败时旧活动引用不变并报告未切换，但只删除 staging 源目录，保留已提交事务引用的目标迁移文件，下次 `/resume` 可采用该事务；任何路径均不覆盖或删除目标已有文件。（AC10）

## 会话恢复与清理

- [ ] 准备多个“文件 mtime 顺序”和“最后有效记录活动时间顺序”不同的 JSONL，通过 `/resume` 主动选择会话；预期列表只扫描 `.novacode/sessions/*.jsonl` 并按有效记录时间倒序展示 session ID、首条用户消息摘要、模型、最后活动时间和大小，恢复后以原 session ID 继续写回原 JSONL 和工具目录。（AC11）
- [ ] 分别恢复最后活动时间为 23 小时 59 分和 24 小时 1 分的会话；预期前者无提醒，后者只在当前上下文末尾获得 system 语义的过期提醒，原 JSONL 不新增伪造的历史消息。（AC12）
- [ ] 分别准备最后有效记录时间为 29 天 23 小时和 30 天 1 分的会话，再准备无有效记录但 JSONL/tool-results mtime 位于边界两侧的空或全损坏会话，并在清理期间持续操作主界面；预期同步磁盘 worker 通过 `asyncio.to_thread()` 或清楚等价方式在后台执行，有效会话按记录时间、无效会话按 `max(JSONL mtime, tool-results mtime)` 判定，只删除严格超过 30 天者，单项失败被隔离且主界面保持可响应。（AC13）

## 长期记忆：显式工具与隐式提取

- [ ] 用 `manage_memory` 分别 create `user`、`feedback`、`project`、`reference`；预期前两类只写用户目录，后两类只写项目目录，create UUID 由代码生成，schema 不暴露 filename/path。再验证 Default/Accept Edits/Plan/Bypass 为 Ask/Allow/Deny/Allow，成功、拒绝、锁失败和事务失败均产生准确终态。（AC14、AC17）
- [ ] 创建一条记忆并检查独立 Markdown 文件与同目录 `MEMORY.md`；预期 frontmatter 至少包含稳定 ID、`type`、`title`、`created`、`updated`，正文可独立理解，索引项包含类型、标题、摘要和可点击相对链接且不嵌入完整正文。（AC15）
- [ ] 构造 `MemoryExtractor` 后先确认未启动 worker 且绑定前拒绝 submit；TUI provider 选择成功后一次性绑定并只启动一个消费者，再绑定不同 provider 被拒绝。随后快速完成三轮不含待执行工具调用的最终回复并延迟第一项任务；预期每轮立即且仅入队一次，回复显示和下一轮输入不等待，提取请求 `tools=[]`，后续任务在前一项提交或失败收尾后按顺序启动并重新读取最新两级索引；关闭时停止接收并排空或受控取消。（AC16）
- [ ] 显式请求覆盖 create/update/delete、非法字段、错误 ID、索引刷新，以及模型未调用工具却回复“已记住”的场景；预期只有成功工具结果允许确认，其他情况替换为“记忆未写入”，普通记忆系统咨询保持原回复。后台提取提示包含完整字段契约、四类定义、action 必填字段、去重和纯 JSON 约束，显式请求不得返回空数组且严格解析不接受代码围栏。（AC17）
- [ ] 验证全部 txn 临时文件使用唯一 ID：note/index `fsync` 后写 `.memory-transaction.<txn>.tmp`，`fsync` 后以 `os.replace()` 发布 `.memory-transaction.json` 并 fsync 目录，正式 journal 出现前不替换正文。覆盖发布前崩溃、发布后各阶段、无正式 journal 孤儿清理；损坏/校验失败/越界 journal 必须标记 recovery-required、阻断提取和治理写入并保留文件，不得静默删除。200 行/25KB 预演保持不变。（AC18）

## 记忆治理

- [ ] 依次构造 memory 目录不存在、距上次成功治理未满 24 小时、距上次实际扫描未满 10 分钟、可恢复会话少于 5 个、`.consolidate-lock` 获取失败五种场景，再构造五项全部满足的场景；预期先用旧 `_last_scan_at` 判断 10 分钟节流，被节流时不更新时间，通过节流并真正开始扫描时立即更新，随后即使目录/24 小时/会话数/锁门控失败也保留新时间；任一门控失败均不创建任务，全部通过时只创建一个。（AC19）
- [ ] 分别创建活跃 PID 且锁龄超过 1 小时、死 PID、PID 状态未知且未超过 1 小时、PID 状态未知且超过 1 小时的锁，并并发发起两个检查；预期活跃 PID 锁始终保留，死 PID 锁和未知但超时的锁可原子回收，未知且未超时的锁保留，并发检查最多一个获得锁。（AC20）
- [ ] 获取治理锁后记录原 mtime，分别模拟成功、异常和取消；预期成功治理更新 mtime，异常或取消恢复旧 mtime，后续门控仍以最近一次成功治理时间判断。（AC21）
- [ ] 让受限治理子 Agent 读取会话与两级记忆并修改本次目标 memory 目录，同时尝试执行 shell、修改项目源码和写入边界外路径；预期只允许目标 memory 目录内的合规变更，三类越权请求被拒绝且已允许变更不受影响。（AC22）
- [ ] 在治理运行期间连续发送消息，并分别模拟治理完成和失败；预期启动、Agent Loop 和输入均不被阻塞，主会话在每次任务结束后只收到一条包含状态及 create/update/delete 数量的简短通知，完成或失败通知均不包含记忆正文。（AC23）

## 集成

- [ ] 分别传入空和非空的指令、用户级索引、项目级索引，检查系统 prompt；预期空索引时仍保留 `manage_memory`、成功依据和禁止 `write_file/edit_file` 创建 `.nova_memory.md` 的规则，非空索引只追加用户级在前、项目级在后的索引，空指令仍省略。（AC24）
- [ ] 在无指令、无 memory 目录、无历史会话，以及后台清理或治理抛错的场景启动；预期 `NovaCodeApp` 可完成 provider 选择并进入交互状态。另验证 writer 初始化失败不进入 TUI，writer model 绑定或 extractor provider 绑定失败时输入保持禁用；只有依次完成 writer 绑定、extractor 一次性绑定/单 worker 启动和 Agent 创建后才开放提交。（AC25）
- [ ] 用自动化生命周期测试区分 compact commit 前后：commit 前失败清理 staging 和清单内本次迁移文件且目标 JSONL 不变；commit 后 runtime 切换失败只删 staging，保留目标迁移文件与已提交事务，旧活动引用不变并可再次 `/resume`。同时验证 writer/extractor 排空、无跨 session 写入及旧 writer 关闭失败不回滚。（AC26）
- [ ] 检查实现依赖、目录结构、运行数据流和验收边界；预期继续复用 `NovaCodeApp`、prompt 模块槽位、文件工具、`Conversation.replace_history()`、`src/novacode/compact/` 与 ch08 工具结果机制，没有引入 Spec 排除的数据库、向量检索、额外恢复命令或平行压缩实现；四类精确路由、提取延迟、治理门控/异常和崩溃注入均由自动化测试确定性覆盖，tmux 不承担这些模型不可控断言。（AC27）

- [ ] 在两个进程中分别覆盖 extractor/extractor 与 extractor/governor 竞争：`recover_locked()`、`apply_locked()`、完整提取事务和治理写阶段均同时持有进程内锁与对应 `.memory-write.lock`；两级目录始终用户级→项目级获取、反向释放，活 PID 不抢占、死 PID安全回收，且 `.consolidate-lock` 仅负责治理调度。（AC16、AC19、AC20）

## tmux 端到端

> 仅当 Linux/WSL、仓库 Linux `.venv`、tmux、真实 provider 配置均可用，且本清单已与 AC1–AC27 对照一致时执行。必须使用临时 HOME、临时 workspace 和独立 tmux socket；只把 provider 启动必需配置复制到临时 HOME 的 `.novacode/` 并设权限 600，通过 `PYTHONPATH=<repo>/src` 从临时 workspace 启动，trap/finally 清理临时目录和 socket。不得修改真实 `~/.novacode/`、真实用户指令/记忆或仓库工作树。任一前置不满足时记录“未执行”及具体原因，不得勾选或标记通过。

- [ ] 保存 `REAL_HOME` 和 `REPO`，用 `mktemp -d` 创建临时根，在其中创建 HOME/workspace/socket；用 `install -m 600` 复制 provider 所需配置，注册 trap/finally，使用 `tmux -S <temp-socket>` 创建持久 shell，并发送 `HOME=<temp-home> PYTHONPATH=<repo>/src <repo>/.venv/bin/python -m novacode` 冷启动。预期只在临时 workspace 创建会话数据，首条记录含 model，消息文件和工具结果目录共享 ID，真实 HOME 与仓库 `git status` 不变。（AC6–AC8、AC25、AC27）
- [ ] 只在临时 HOME/workspace 配置四层可区分指令和一个合法独占行 `@rules/style.md`，重启真实对话；预期可观察到低到高的基本优先级及合法引用内容。精确展开深度、环路、越界、二进制与失败诊断只使用 `tests/instructions/test_loader.py` 的确定性证据，不要求真实模型逐项表现。（AC1–AC5、AC27）
- [ ] 在 tmux 中请求 NovaCode 读取项目文件并完成至少一次真实工具调用，预期 JSONL 保存 user、带完整 tool calls 的 assistant、按 ID/顺序配对的 tool results 和最终 assistant；最终回复显示后输入立即可用。（AC7、AC9、AC16）
- [ ] 输入“记住我目前的方向是 Agent 开发，最常使用 Python”，在 Default 模式批准一次 `manage_memory`；预期临时 HOME 的 `.novacode/memory/` 生成 `MEMORY.md` 和独立 UUID 记忆文件，workspace 无 `.nova_memory.md`，会话记录未调用 `write_file/edit_file`。正常 `/exit` 后重启并询问常用语言，预期从索引召回 Python。（AC14、AC17、AC24、AC27）
- [ ] 在 NovaCode 中输入 `/exit` 并等待返回同一持久 shell，再从该 shell 重启并执行 `/resume`；选择刚才会话后完成一轮真实对话，预期恢复完整工具链、使用原 session ID 续写，旧临时会话不接收消息。坏行、未提交事务、staging 失败和崩溃注入以自动化测试为权威证据，不在真实 provider 会话中强制注入。（AC8–AC11、AC26）
- [ ] 在隔离 workspace 准备超过 24 小时的恢复样本并在 tmux 中恢复，预期只向当前上下文注入 system reminder；30 天有效/无效记录清理及线程非阻塞性只使用 `tests/session/test_listing_cleanup.py` 的确定性证据。（AC12、AC13）
- [ ] 先用自动化测试构造并验证完整提交的 ch08 压缩事务，再在 tmux 中 `/resume` 该样本；预期使用原 session ID 恢复已提交替换历史并继续写入原 JSONL/工具结果目录。四类精确路由、提取延迟、治理门控/异常、staging/journal 崩溃注入全部保留为自动化验收，不要求模型确定地产生指定结构化操作。（AC10、AC14–AC23、AC27）
- [ ] 正常 `/exit` 后确认返回持久 shell、旧 JSONL 最后一行完整可解析，记录大小与 mtime；再次启动新会话并完成一轮后，旧文件不再变化且新消息只进入新 ID。结合自动化生命周期测试证明 append 排空、extractor 停止接收并排空或受控取消、活动引用切换和旧 writer 关闭。（AC26）

完成全部证据记录后，由 trap/finally 通过独立 socket 关闭 tmux server 并删除临时 HOME/workspace/socket；该清理不作为 AC26 正常退出或资源排空证据。最后确认真实 HOME 和仓库工作树没有因 tmux 验收发生变化。
