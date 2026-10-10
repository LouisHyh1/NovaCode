# 第七阶段验收报告

2026-10-10 完成 7.1–7.9，整体进度 42/71。12 道自建开发专项已通过独立入库核验，类别为定位/测试补充/工具失败/长上下文/发现 `2/2/2/3/3`，难度为简单/中等/困难 `4/6/2`。本阶段没有实施 Live 开发集筛选、先导校准、冻结或正式统计；完整开发集出口 DEV_ADMITTED 尚未达成。

## 实现与独立验收

| 任务 | 合同与实现 | 关键验收 |
|---|---|---|
| 7.1 / S01–S02 | 配置 CLI 与异步错误传播小仓库；files/symbols/relations JSON | 两条有效路径均接受，错误关系、缺失事实、多个冲突答案及业务修改失败 |
| 7.2 / S03 | 配置默认、覆盖优先级、空字符串和 None；仅新增 unittest | 正确实现通过；反转优先级、丢失空值、错误缺省三个缺陷被检出；生产/已有测试修改、收集错误、跳过与无条件失败拒绝 |
| 7.3 / S04 | started/release 事件同步的异步 Operation | 正常与取消后关闭一次且 idle；缺少关闭、错误恢复被检出；五次机制重复一致，不用 sleep；目标函数实际执行 |
| 7.4 / S05–S06 | 正常权限/Hook 后的一次 grep 错误与标准测试命令真实超时 | 搜索重试恢复，真实子进程握手后取消并核验停止；正确修复和合法替代实现通过，未触发时覆盖为零 |
| 7.5 / S07–S08 | 各六条固定请求、早期公共接口及 1440 行事实文件 | 原接口/新增目标均通过；S08 同时检查四份中间成果和原始事实；所有阶段 Provider 成本合计，无末轮答案重提示或跨配置摘要 |
| 7.6 / S09 | 七条固定请求，迁移→校验→连接三个阶段 | 输入不可变、未知扩展、显式零、旧 host、参数校验和最终返回同时通过；恢复任一早期原文件均失败 |
| 7.7 / S10–S11 | 固定 CI 提交与职责相近的测试/部署记录；内置和真实 stdio MCP | 数据来源不混淆；三配置共享业务数据和能力，真实 MCP 连接/关闭，发现成本完整；合法基础工具替代仍可通过 |
| 7.8 / S12 | 内置 issue 与 MCP CI 关联，normalize 修复 | 正确关联与行为必须同时通过；关联错误或只返回事实未修复均失败；真实模型调用两种业务能力，三次发现计费 |
| 7.9 | EvaluationTask、Qualification、SELECT/ADMIT 与 SHA-256 | 12 题全量正确/负例对照，家族唯一，来源和难度配额无缺口；[入库清单](admission/admitted.json) 与 [原选择账本](admission/selection.jsonl) |

实现位于 [evaluation/specialty](../../../../../evaluation/specialty/README.md)，复用正式 worker/Agent/ContextManager/ToolRunner/SessionService。运行器为每题创建全新容器和 `/specialty-bed` Git 初始提交；HOME、内部 Session、对话与曝光状态均新建。公开初始文件、请求、业务数据与外部答案/缺陷/验收资产分开；无宿主挂载、Docker socket 或本仓库副本进入 Agent 环境。交付源码与实际 wheel 按文件 SHA-256 完全匹配，wheel 不包含定义构建器或隐藏判题资产，见 [只读审计](representatives/review.json)。

判题基于固定 base 的完整补丁，在外部重新构建初始文件，再只将候选生产代码/测试与执行驱动复制到全新干净容器。测试补充题要求实际执行 resolve/run，并从新增测试产生断言失败；导入失败不算检错。初始业务测试本来存在的失败与环境可编译检查分别记录。全部 12 题的环境检查、正确对照和负例均由离线容器取得，资源配置通过 Docker inspect 核对：1 CPU、512 MiB 内存及 Swap 总额、64 PID、网络 none，单执行超时 20 秒；每次判题后强制移除所属容器。镜像提供目标 Python 3.8 环境，NovaCode 使用独立 CPython 3.12.13，不覆盖目标依赖。

每个合同绑定公开初始状态、请求、环境及验收清单；验收清单绑定 judge/test_driver/build/definitions 实现字节。SELECT/ADMIT 的资格证据绑定合同指纹，不依据模型成败入库。实际资格文件为 `assets/external/Sxx/qualification-admission-final3.json`；相应 checks.json 列出每项原始对照与资源证据。每个问题家族独立，Live 入库仍待第八阶段，不能把 12 道专项或一个历史 Live 代表环境称作 24 题就绪。

## 真实模型与机制证据

代表运行均为 Anthropic 协议、deepseek-v4-flash、thinking=true、SDK 零自动重试、90 秒请求超时及 4096 输出上限；使用 full 策略、单主 Agent、隔离 HOME/Session、空记忆与空 Hook，以及正常权限。S12 自动入口查询 issue、发现 CI 能力、修复并返回关联事实；S05 在 tmux 中输入公开固定请求，实际遭遇第一次搜索错误后重试并修复。隐藏验收只在结束、清理和提取后运行，不反馈给同一次 Agent 运行。

| 入口/题目 | Provider 请求 | 工具请求 | 发现 | 故障 | Token | 终止/验收/清理 |
|---|---:|---:|---:|---:|---:|---|
| 自动 / S12 | 8 | 11 | 3 | 0 | 29,269 | completed / passed / passed |
| tmux / S05 | 6 | 7 | 0 | 1 | 21,706 | completed / passed / passed |

两次范围检查通过，strict_success=true，累计 50,975 Token。全部 usage 完整，无未完成工具、截断账本或未关闭阶段；tmux 退出码 0。补丁、最终回答、工具/请求输入、Session 与屏幕按原字节归档；worker.tar.gz 解包后可按账本引用核验。MCP 准备及关闭在 worker 中记录阶段，外层另列环境准备、独立判题和容器清理；不把这两题的时长用于性能收益比较。金额未核验，没有用 Token 数虚构金额。

每次临时上限为 300 秒、200,000 Token、20 Provider/30 工具请求，单请求预留 30,000 Token，清理宽限 20 秒；代表验收总上限为 1200 秒、800,000 Token、80 Provider/120 工具请求。实际只启动两次有模型的代表运行。长上下文全阶段计量、三配置业务数据、S06 真实进程清理和取消重复检查是机制证据；未据此宣称 12 题真实模型成功率、压缩/发现收益或实际摘要覆盖。MCP 注册沿用已安装 v1 SDK，并按 Context7 官方文档核验 stdio/list_tools/call_tool 契约。

人工抽查核对了 S01 替代路径和反向关系、S03/S04 错误阶段、S05 分支式替代修复、S08 中间成果、S09 覆盖早期文件以及 S12 关联与修复联合条件。检查目的是发现判题误判，主计分仍为自动合同；未人工将模型失败改判为成功。

## 复跑与质量

快速机制复核为 `.venv/bin/pytest -q tests/evaluation/test_specialty.py`，本轮 40 passed。外部入库复跑为 `.venv/bin/python evaluation/specialty/validate.py --assets evaluation/specialty/assets --output <全新核验目录>`，不发起模型调用。新增核验使用新身份和独立文件，不覆盖旧选择账本或失败记录；公开/隐藏包重建命令见专项 README。

真实代表复跑使用 `.venv/bin/python evaluation/specialty/run.py --assets evaluation/specialty/assets --campaign <全新外部目录> --archive <当前源码与锁文件构建的runtime.tar> --run auto-s12 --task S12`，tmux 对应 `--run tmux-s05 --task S05 --tui`；这些命令会调用真实模型，受上述固定有限预算限制。runtime wheel 必须与交付源码一致，依赖沿用 stage-1 精确 wheel 集合；当前 runtime 指纹在各运行 source-identity.json 中。只读导出为 review.py，调用参数与当前交付的脚本对应。

最终全量 [pytest](pytest.txt) 为 913 passed；[Ruff](lint.txt)、[格式](format.txt)、[严格 mypy](mypy-evaluation.txt)、[复杂度](complexity-evaluation.txt)、[架构增量门禁](boundaries.txt)、[锁文件](lock.txt)及启动检查全部通过。新增评测工具由已有 evaluation 门禁覆盖，七个外部专项脚本加入增量范围；只扩展目标，不增加历史诊断额度，Linux mypy 179、新增 0，C901 20、新增 0。Windows 本地专项仍待第十二阶段，WSL 结果不代替 Windows 证据。

版本由 0.1.31 递增至 0.1.32，pyproject/runtime/uv.lock/installed metadata [一致](version-identity.json)，锁文件只改变项目自身版本。[质量命令](quality-check.py) 与 [退出码](quality-results.json) 可复核；OpenSpec 严格校验另存 openspec.txt。旧调试记录保留于 debug 归档：首次 Docker stdin 目标路径错误、Snap 对宿主 /tmp 的不可见路径、尚未补充逐题环境的审计，以及 S06 原始目标失败被环境检查错误混入的一次尝试；最终使用 tar 流提取、独立环境 smoke 和新资格身份，没有重写失败日志。

本轮验证后停止在第七阶段，只提交本阶段源码、测试、资产、证据、任务进度与版本。未读取、审查或修改 docs/，未归档整个 change，未进入第八阶段或正式实验。
