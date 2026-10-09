# 第一阶段源码核验与环境阻塞记录

本文件保留首次源码核验与 Docker 权限拒绝时的记录；用户修复后已完成第一阶段，当前结论和后续验证见 [environment-ready.md](environment-ready.md)。下文中的阻塞和未执行项描述首次检查状态。

核验日期：2026-10-09。源码基线为 `975591d79efb4f5aa97c32e6a18a0b36a0e92b3a`，分支为 `eval/reproducible-agent-evaluation`，开始时 Git 工作区和索引均为空。源码及依赖文件的 SHA-256 见 [source-fingerprints.sha256](source-fingerprints.sha256)，只读命令、退出码与实际资源见 [preflight.json](preflight.json)。这是前置证据，不是评测运行结果。

## 1.1 源码调用关系

下表位置均相对项目根，行号对应上述基线；只核验当前实现，不代表后续评测功能已经实现。

| 范围 | 源码位置 | 核验结果与后续复用边界 |
|---|---|---|
| CLI 入口 | `pyproject.toml:17`、`src/novacode/cli.py:35`、`:41` | `nova` 调用 `main()`，通过 `asyncio.run(_amain())` 加载配置并装配产品组件。 |
| 产品装配 | `src/novacode/cli.py:81`、`:110`、`:114`、`:185`、`:215` | CLI 装配 Session、指令、记忆、权限、Hook、六个工具及辅助角色；MCP 先连接再注册，最终构造 `NovaCodeApp`。评测主集需隔离操作者状态并限制辅助能力，不能照搬真实用户状态。 |
| Provider 创建与共享 | `src/novacode/llm/__init__.py:116`、`src/novacode/tui/app.py:288`、`:325` | TUI 调用 `new_provider()`，将同一 Provider 传给 Agent、记忆组件及 Team；Session 绑定正式 Agent。 |
| TUI 驱动 | `src/novacode/tui/app.py:943`、`:968`、`:999` | `_start_stream()` 在 `with_cwd()` 内创建任务，直接消费 `agent.run()` 事件。 |
| 正式循环与非交互接口 | `src/novacode/agent/__init__.py:338`、`:658` | `run_to_completion()` 添加公开请求后调用同一个 `run()`，透传错误，达到轮数上限抛异常。后续入口必须复用此循环；本次未新增执行引擎。 |
| 上下文共享事务 | `src/novacode/agent/__init__.py:223`、`:315`、`:393`、`:472`；`src/novacode/agent/context_manager.py:92` | 自动、手动、紧急入口共用 `ContextManager.prepare()`，先 Layer 1，再根据触发与阈值决定 Layer 2；真实摘要才派发 PRE/POST_COMPACT，接受历史后更新锚点。当前没有统一压缩关闭开关。 |
| Session 恢复 | `src/novacode/session/service.py:155`、`:307`、`:317` | 恢复候选根据上下文阈值调用正式 Agent 的 `run_force_compact()`，使用暂存运行状态；后续策略需覆盖此路径。 |
| 主调用与摘要 | `src/novacode/agent/__init__.py:693`、`:711`；`src/novacode/compact/layer2.py:64`、`:75` | 主调用和摘要都使用借用的 Provider；摘要仅消费文本和错误，没有消费 usage，超限可触发显式摘要重试。后续完整账本需在 Provider 边界观测。 |
| 当前 Anthropic 请求规则 | `src/novacode/llm/anthropic_provider.py:23`、`:45`、`:69`、`:78` | 构造 SDK 客户端时没有显式传重试/超时参数；请求输出上限为 4096，仅在 thinking 开启且无工具历史时发送 2048 思考预算。这是源码规则，未作端点或 SDK 实测。 |
| Schema 导出 | `src/novacode/tool/__init__.py:100`、`:113`；`src/novacode/agent/__init__.py:305`、`:359` | Registry 按注册顺序导出完整定义，Plan 导出只读定义，Agent 进一步过滤允许集合；当前在每个 Run 开始获取一次，尚无按轮发现曝光机制。 |
| 六个基础工具与工作目录 | `src/novacode/tool/__init__.py:61`；`src/novacode/tool/ctx.py:12`、`:34` | 基础工具为 read_file、write_file、edit_file、bash、glob、grep；相对路径使用上下文目录或进程 cwd。本次尚未证明容器内 `/testbed` 与目标解释器一致。 |
| 工具权限、Hook 与执行 | `src/novacode/agent/tool_runner.py:92`、`:162`、`:178`、`:207`、`:241`、`:279` | 工具依次经过前置 Hook/允许集合/Plan 检查、权限检查、Registry 执行、后置 Hook；连续只读调用使用 asyncio 并发。不能用另一套执行器或权限绕过替代。 |
| 工具观测缺口 | `src/novacode/agent/__init__.py:87`；`src/novacode/agent/tool_runner.py:37`、`:147` | ToolEvent 参数是 80 字符预览且无 call_id；底层 ToolCall/ToolResult 有关联 ID，后续观测应保留。 |
| Provider 所有权与关闭 | `src/novacode/tui/app.py:1243`、`:1299`、`:1305`；`src/novacode/cli.py:242` | TUI 关闭方法有幂等标记，先收尾组件和 Session，再关闭 Provider；CLI 负责外围 MCP/Hook 收尾。Agent 和上下文模块借用 Provider。此处为静态核验，取消/失败关闭验证在后续阶段。 |
| 当前版本 | `pyproject.toml:3`、`src/novacode/__init__.py:1`、`uv.lock:615` | 三个版本来源及已安装 metadata 均为 0.1.25，Python 为 3.12.13；版本与锁检查结果见原始证据。设计中的 0.1.24/287dd45 是历史观察身份，未作为当前基线。 |

全过程未读取、审查或修改项目 `docs/`，未修改生产代码、权限、容器服务或用户记忆；未引入第二套执行引擎。初始工作区干净，本次只新增本目录证据并更新任务 1.1。没有修改历史规划与其他阶段任务。

## 1.2 环境阻塞

当前身份为 WSL Ubuntu-22.04 的 `louishyh`（uid/gid 1000）。`/var/run/docker.sock` 为 `root:root`、模式 0660；`docker version` 返回非零退出码和 `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`。只能确认客户端可启动，不能确认 daemon 健康、镜像可访问或当前用户能执行容器；不尝试通过提权、修改 socket 或添加用户组绕过拒绝。

宿主可见 CPU 为 4、内存为 6215159808 字节、Swap 为 2147483648 字节；磁盘空闲与检查时间见原始记录。cgroup2 根下没有可读取的 `cpu.max`、`memory.max`，因此有效容器 CPU/内存/磁盘上限均未知，不能用宿主容量代替配置上限。

阶段出口为环境阻塞，`ENV_READY=false`。任务 1.2 保持未完成；1.3 的官方 Python 判题 commit/隔离环境、1.4 的镜像 digest/原始失败/参考补丁三次成功/峰值资源、1.5 的容器内独立 Agent 解释器均未取得证据，不勾选，不产生 Agent 成败样本。

未发起模型调用或真实 tmux 对话。`tasks.md` 1.2 明确要求“权限拒绝时明确阻塞而不发起模型调用”，runner 规范也要求停止此类任务调用；第一阶段尚未验证通过，故用户要求的后续版本递增、提交及推送尚未执行。

继续前需提供当前用户可访问的 Linux 容器执行环境，再复查访问身份与资源上限，依次完成 1.2–1.5。当前暂停依据 `openspec-apply-change/SKILL.md` 的 “Pause if: Error or blocker encountered”，不是缺少既有实施或提交授权。

## 本次验证边界

`uv lock --check`、`python -m novacode --version` 和 `openspec validate add-reproducible-agent-evaluation --strict` 均通过。四个版本来源一致，源码指纹逐项复核，`git diff --check` 通过。这些检查验证源码核验产物和规划格式，不代表第一阶段 ENV_READY 或容器判题通过。未改生产实现，因此未运行完整回归；真实 tmux 对话待环境前置通过后执行。
