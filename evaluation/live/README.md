# Live 开发题入库

本目录负责固定 SWE-bench-Live verified 版本的完整池审查、离线资格验证、公开包构建及独立判题。生产 Agent 沿用现有装配和主循环；这些脚本不另建执行引擎。题库入库不等于真实模型解题实验或正式榜单成绩。

`selection-rule.json` 保存事前种子、仓库范围、每仓库上限、标题初分层及认知审查。完整数据逐行留证，冲突 ID 的所有行排除；标题层仅是候选组织方式，最终候选必须先通过认知跨度审查，再按同层固定顺序核验和替补。修改行数不作为难度判据。补充审查保存在外部根的 `additional-complexity-reviews.json`，不得改变已确定层或排序；全部排除原因与原始失败保留。首次测试前的镜像下载断流/超时最多恢复三次，每次使用新资格身份；一旦开始原始或参考验收则不适用此准备重试，避免选择三次最好结果。

公开包仅有 `requests.json`、`initial.json`、`environment.json`，请求精确保留原始 problem_statement。含参考补丁、隐藏测试、hints 和目标测试名单的 row 及 judge 存在 `external/`。执行 Agent 所在容器只接收公开请求和独立 NovaCode 运行时，不挂载本仓库、外部 row、判题代码或 Docker socket。外部判题在提交收尾之后进入全新干净容器，结果不反馈给同一次 Agent 运行。

本轮资格还要求官方镜像初始 Git 工作树为空白。非空白可能是已跟踪源码/测试修改，也可能只是未跟踪的预置产物；以 `initial-image-state.json` 的实际 status 区分，不将未跟踪文件称作测试作弊。不在核验时清理文件后重新判断，也不对失败候选放宽这项规则。

资格验证与判题共用 `judge.create()`：网络模式为 none，允许本地 loopback，禁止外网；固定 2 CPU、4 GiB 内存及 Swap 总额、256 PID，单次官方完整命令上限 600 秒。每题原始目标失败且官方 PASS_TO_PASS 全部通过，参考补丁三次通过；第三次先部署独立 CPython 3.12+ NovaCode，确认目标解释器、依赖和源码身份未改变，六个正式工具正常，再执行官方判题。磁盘为宿主共享文件系统，入库前至少保留 20 GiB，无独立容器磁盘配额；资源原值与峰值留在原始证据中。

准备依赖沿用第一阶段固定 commit 的官方 Python grader 与独立环境，包含 docker、pyarrow 和官方 swebench，不将它们加到产品依赖中。下面命令中的 `GRADER` 表示该独立 Python，`DATA` 为固定 revision 数据，`ROOT` 为一个全新的外部目录，`RUNTIME` 为当前源码与锁定 wheel 的独立运行时 archive；不得覆盖旧资格目录或账本。

```bash
PYTHONPATH=src "$GRADER" evaluation/live/prepare.py --dataset "$DATA" --output "$ROOT/pool-final"
.venv/bin/python evaluation/live/curate.py select --pool "$ROOT/pool-final" --archive "$RUNTIME" --grader "$GRADER"
.venv/bin/python evaluation/live/curate.py build --pool "$ROOT/pool-final" --output evaluation/live/assets --grader "$GRADER"
.venv/bin/python evaluation/live/review.py --assets evaluation/live/assets --root "$ROOT" --output "$EVIDENCE"
```

本轮缓存数据位于 `~/.cache/novacode-evaluation/stage1/verified.parquet`。选择器目前沿用该固定相邻 stage1 缓存位置，不支持任意数据路径；准备步骤仍校验 revision 的官方 LFS 身份与实际字节。构建资产中的判题命令记录本机独立 Python 与资产绝对路径，迁移机器须重新构建合同并重新核验，不能保留失效指纹。

Registry 返回 HTTP 429 时暂停整个选择过程，不将拉取额度耗尽记作候选无效，也不立即循环重试。额度恢复后使用新的外部准备目录，复制固定池、运行时身份与补充审查；`transport-policy.json` 的 `reused_test_attempts` 绑定此前首个已开始测试的资格目录。仅重新准备未开始测试的镜像，全部旧记录保留。

已登录 Docker 后，OCI 准备通道从 `DOCKER_CONFIG/config.json`（未设置时为 `~/.docker/config.json`）读取 Docker Hub 的 `auth` 字段，仅向官方 auth.docker.io 换取当前仓库的短期 Bearer 令牌，不导出凭据。本轮 Snap CLI 的配置位于 `~/snap/docker/current/.docker`；该路径只用于宿主准备进程，不复制进 Agent 或判题容器。若 Docker 登录使用 credential helper 而无内联 auth，使用 Docker 原生下载通道；当前备用脚本不解析 helper。

`smoke.py` 复用专项阶段的正式运行器及 tmux 键盘/屏幕/退出驱动，执行一题产品验收；它会调用真实模型。其临时上限为 300 秒、400,000 Token、20 Provider/30 工具请求，单请求预留 30,000 Token，清理宽限 20 秒，总上限 1200 秒、800,000 Token、80 Provider/120 工具请求。该单题验收不启动 24 题三配置先导或 432 次正式实验；先导、冻结及正式预算仍由后续阶段负责。

首次 tmux 验收使用 200,000 Token 临时上限并预算停止，原结果保留。后续单次产品验收使用上述 400,000 上限，沿用同一 800,000 总预算和相同公开输入；它们分别报告，不用于预算不一致的配置比较。

Docker 原生下载持续断流时，准备进程可采用 `fetch_image.py`：从官方 Registry 获取并核对 manifest，按大小和 SHA-256 验证每个 blob，最多三次 Range 恢复后构建 OCI 归档，再以 Docker 原生 load 导入；额外核对 RepoDigests 与 rootfs diff_ids。该通道保持镜像字节身份，首个验收之前仍使用 600 秒外层上限。新的 `transport-policy.json` 记录通道及已实际验收目录的复用映射；原始/参考验收失败不重试，下载日志中的临时签名 URL 在交付副本中脱敏，原始局部记录继续保留。

当前交付为 12 道 Live 开发题，合同位于 `assets/contracts/`，与 12 道专项共同达到 `assets/admitted.json` 的 DEV_ADMITTED。难度 4/6/2，覆盖 8 个仓库，每仓库最多 2 道；31 个候选的固定顺序、失败与替补见 [第八阶段报告](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-8/live-validation.md)。先导、冻结及正式实验尚未开始。准备进程中断且没有原始/参考目录时，保留已有文件并以 PreparationInterrupted 补记终态，最多恢复到第三份准备身份；已经开始的验收不适用该重试。后台控制器可在独立 tmux 会话执行，日志存持久缓存，不依赖 `/tmp`。
