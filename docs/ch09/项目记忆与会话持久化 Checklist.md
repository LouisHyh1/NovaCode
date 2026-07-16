# 项目记忆与会话持久化 Checklist

> 本清单是 `项目记忆与会话持久化 Spec.md` 中 AC1–AC27 的执行镜像。Spec 是唯一权威验收依据；若本清单与 Spec 冲突，以 Spec 为准并先修正本清单。每项必须记录实际命令、退出码、关键输出或文件证据；未执行的项目保持未勾选，并写明原因。

## 静态质量

- [ ] 在 Windows 项目根运行 `.\.venv\Scripts\python.exe -m pytest tests/instructions/test_loader.py -q`、`.\.venv\Scripts\python.exe -m pytest tests/session/test_writer.py tests/session/test_reader.py tests/session/test_listing_cleanup.py -q`、`.\.venv\Scripts\python.exe -m pytest tests/memory/test_store.py tests/memory/test_extractor.py tests/memory/test_governor.py -q`，预期三组新增模块测试均退出码为 0，并分别覆盖指令、会话、记忆提取与治理边界。（AC1–AC23）
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
- [ ] 触发 ch08 压缩并检查同一 JSONL 依次追加同事务 ID 的 `compact_begin`、完整替换历史和 `compact_commit`；分别在 begin 后和部分替换历史后模拟中断，预期只有 commit 已 fsync 且事务 ID、数量、序号、摘要一致的事务替换内存并在恢复时采用，未提交事务回退到事务前完整历史；超阈值恢复也复用同一压缩流程。（AC10）

## 会话恢复与清理

- [ ] 准备多个“文件 mtime 顺序”和“最后有效记录活动时间顺序”不同的 JSONL，通过 `/resume` 主动选择会话；预期列表只扫描 `.novacode/sessions/*.jsonl` 并按有效记录时间倒序展示 session ID、首条用户消息摘要、模型、最后活动时间和大小，恢复后以原 session ID 继续写回原 JSONL 和工具目录。（AC11）
- [ ] 分别恢复最后活动时间为 23 小时 59 分和 24 小时 1 分的会话；预期前者无提醒，后者只在当前上下文末尾获得 system 语义的过期提醒，原 JSONL 不新增伪造的历史消息。（AC12）
- [ ] 分别准备最后活动时间为 29 天 23 小时和 30 天 1 分的会话及其同 ID 工具目录，并在清理期间持续操作主界面；预期后台只删除严格超过 30 天的 JSONL 与工具目录，单项失败被隔离，主界面保持可响应。（AC13）

## 自动记忆

- [ ] 让提取模型分别返回 `user`、`feedback`、`project`、`reference` 的 create 操作；预期前两类只写入 `~/.novacode/memory/`，后两类只写入 `<project_root>/.novacode/memory/`，模型不能自行改变目标层级。（AC14）
- [ ] 创建一条记忆并检查独立 Markdown 文件与同目录 `MEMORY.md`；预期 frontmatter 至少包含稳定 ID、`type`、`title`、`created`、`updated`，正文可独立理解，索引项包含类型、标题、摘要和可点击相对链接且不嵌入完整正文。（AC15）
- [ ] 快速完成三轮不含待执行工具调用的最终回复，并延迟第一项提取任务；预期每轮最终回复后立即且仅入队一次，回复显示和下一轮输入不等待，提取请求 `tools=[]`，后续任务在前一项提交或失败收尾后按顺序启动并重新读取最新两级索引。（AC16）
- [ ] 分别提交合法 create、update、delete、no-op、语义重复内容、类型或目标层级非法的 create 以及越界文件名；预期前三种操作正确更新文件和索引、no-op 不变更，重复内容由读取完整最新索引的模型选择 no-op 或合并到既有条目，非法或越界操作被拒绝且主会话继续运行。（AC17）
- [ ] 分别让 create 导致 `MEMORY.md` 第 201 行和 UTF-8 大小超过 25KB，再先用 delete 或 update/merge 释放容量后重试；预期超限操作在锁内提交前被拒绝并保持文件和索引原样、不调度治理，只有候选索引同时不超过 200 行和 25KB 时才提交，prompt 始终只注入合规索引及相对链接。（AC18）

## 记忆治理

- [ ] 依次构造 memory 目录不存在、距上次成功治理未满 24 小时、距上次实际扫描未满 10 分钟、可恢复会话少于 5 个、`.consolidate-lock` 获取失败五种场景，再构造五项全部满足的场景；预期任一门控失败均不创建后台任务，只有全部通过时创建一个任务，实际扫描后即使后续门控失败也保留新的扫描时间。（AC19）
- [ ] 分别创建活跃 PID 且锁龄超过 1 小时、死 PID、PID 状态未知且未超过 1 小时、PID 状态未知且超过 1 小时的锁，并并发发起两个检查；预期活跃 PID 锁始终保留，死 PID 锁和未知但超时的锁可原子回收，未知且未超时的锁保留，并发检查最多一个获得锁。（AC20）
- [ ] 获取治理锁后记录原 mtime，分别模拟成功、异常和取消；预期成功治理更新 mtime，异常或取消恢复旧 mtime，后续门控仍以最近一次成功治理时间判断。（AC21）
- [ ] 让受限治理子 Agent 读取会话与两级记忆并修改本次目标 memory 目录，同时尝试执行 shell、修改项目源码和写入边界外路径；预期只允许目标 memory 目录内的合规变更，三类越权请求被拒绝且已允许变更不受影响。（AC22）
- [ ] 在治理运行期间连续发送消息，并分别模拟治理完成和失败；预期启动、Agent Loop 和输入均不被阻塞，主会话在每次任务结束后只收到一条包含状态及 create/update/delete 数量的简短通知，完成或失败通知均不包含记忆正文。（AC23）

## 集成

- [ ] 分别传入空和非空的指令、用户级索引、项目级索引，检查系统 prompt；预期非空内容进入独立的 `自定义指令` 与 `长期记忆` 模块，空内容省略对应模块，长期记忆只含索引且用户级在前、项目级在后。（AC24）
- [ ] 在无指令、无 memory 目录、无历史会话，以及后台清理或治理抛错的场景启动；预期 `NovaCodeApp` 均能进入交互状态并隔离记录后台错误；另验证 writer 初始化或 model 绑定失败时不开放消息提交。（AC25）
- [ ] 用自动化生命周期测试在 append 临界区内切换会话或退出，并给旧 writer 的关闭操作注入失败；预期测试证明先停止旧提交并排空已进入临界区的 append，再原子切换活动引用并关闭旧句柄，消息不会跨 session ID，后台任务不会写入错误项目；原子切换后旧 writer 关闭失败只记录且不回滚新会话。（AC26）
- [ ] 检查实现依赖、目录结构和运行数据流；预期继续复用 `NovaCodeApp`、prompt 模块槽位、文件工具、`Conversation.replace_history()`、`src/novacode/compact/` 与 ch08 工具结果机制，没有引入 Spec 排除的数据库、向量检索、额外恢复命令或平行压缩实现。（AC27）

## tmux 端到端

> 仅当 Linux/WSL、Linux 项目 `.venv`、tmux、真实 provider 配置均可用，且本清单已与 AC1–AC27 对照一致时执行。使用 `.venv/bin/python -m novacode` 启动；任一前置不满足时，以下项目必须记录“未执行”及具体原因，不得勾选或标记通过。

- [ ] 在 Linux/WSL 项目根先运行 `tmux new-session -d -s novacode-ch09 -c "$PWD"` 创建持久 shell，再运行 `tmux send-keys -t novacode-ch09:0.0 '.venv/bin/python -m novacode' Enter` 启动 NovaCode，并用 `tmux attach-session -t novacode-ch09` 进入冷启动；发送真实请求后输入 `/exit`，预期仅创建一个 `.novacode/sessions/<session_id>.jsonl` 消息文件，首条记录含 model，user 与最终 assistant 均可逐行解析，工具结果目录与消息文件共享同一 ID，NovaCode 退出后返回仍然存在的 shell。（AC6–AC8、AC25、AC26）
- [ ] 配置四层互相冲突的指令并在 tmux 中重启对话，预期模型行为遵循低到高拼接顺序且 `NOVACODE.local.md` 覆盖其余三层；再验证独占行 `@rules/style.md`、段落内 `@`、越界和二进制引用，预期仅合法独占行展开，边界拒绝不阻断对话。（AC1–AC5）
- [ ] 在 tmux 中请求 NovaCode 读取项目文件并完成至少一次真实工具调用，预期 JSONL 保存 user、带完整 tool calls 的 assistant、按 ID/顺序配对的 tool results 和最终 assistant；最终回复显示后输入立即可用。（AC7、AC9、AC16）
- [ ] 在另一个 tmux pane 于普通追加期间终止进程，并分别准备 JSONL 坏行和未提交压缩事务后重启 `/resume`；预期坏行被隔离、完整记录和最后完整工具边界得到恢复、未提交压缩事务被忽略，恢复后仍向原 session ID 续写。（AC8–AC11）
- [ ] 准备超过 24 小时的恢复样本和超过 30 天的会话及工具目录，在 tmux 中恢复并持续交互；预期仅前者获得当前上下文 system 提醒，后者由后台清理删除且交互不被阻塞。（AC12、AC13）
- [ ] 在一个会话中提供可分别形成 `user`、`feedback`、`project`、`reference` 的真实信息，等待后台提取完成后退出并冷启动新会话；预期四类记忆进入固定的两级目录，索引只含相对链接，新会话 prompt 读取索引后能按需通过文件工具使用对应正文。（AC14、AC15、AC17、AC18、AC24）
- [ ] 快速连续完成多轮真实对话并让首个提取任务延迟，预期每轮都入队、最终回复和下一轮不等待，提取仍由单消费者串行处理，后续任务读取前项提交后的最新索引。（AC16、AC23）
- [ ] 准备满足治理门控的样本与重复、过时记忆，在 tmux 中触发治理；预期 `.consolidate-lock` 的 24 小时、10 分钟、5 个会话和 PID/mtime 规则生效，治理合并重复、删除过时项，只写目标 memory 目录，并以一条计数通知结束；再模拟失败，预期恢复旧 mtime 且主流程不阻塞。（AC19–AC23）
- [ ] 在 tmux 中触发 ch08 压缩，确认完整追加式压缩事务提交后退出；重新启动并通过 `/resume` 选择原会话，预期用原 session ID 恢复已提交替换历史、继续写入原 JSONL 和工具结果目录，且没有建立平行压缩格式。（AC10、AC11、AC26、AC27）
- [ ] 在 NovaCode 中输入 `/exit` 并等待返回持久 shell，验证旧会话 JSONL 的最后一行是完整可解析的 JSON，并记录该文件的大小与 mtime；再用 `tmux send-keys -t novacode-ch09:0.0 '.venv/bin/python -m novacode' Enter` 启动新会话、完成一轮对话后复查旧文件，预期旧文件大小与 mtime 均不再变化且新消息只进入新 session ID；结合自动化生命周期测试，证明已进入临界区的 append 被排空、活动引用完成切换、旧 writer 随后关闭，后台提取与治理受控取消或收尾。（AC26）

完成全部证据记录后，运行 `tmux kill-session -t novacode-ch09` 只清理 tmux 会话；该命令不作为 AC26 正常退出或资源排空的验收证据。
