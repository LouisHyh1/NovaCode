# 第一阶段验收报告

2026-10-09 第一阶段 1.1–1.5 全部通过，阶段出口为 `ENV_READY=true`，整体进度为 5/71。后续阶段未实施。该结论仅证明一个 CPU 代表题的容器、官方判题和独立 NovaCode 运行环境可用，不是模型解题成功率或完整题库就绪。

## 源码与执行身份

源码核验位置、调用关系及版本基线见 [source-audit.md](source-audit.md)，首次权限拒绝见 [preflight.json](preflight.json)。两份文件保留为历史快照；用户修复后，当前用户加入 docker 组，socket 为 `660 root docker`，当前身份与 Docker 服务记录见 [environment-access.json](environment-access.json)。Docker 服务为 29.8.0，WSL 可见 4 CPU、6215159808 字节内存。

正式产品源码核验与容器中的 NovaCode 验收基线为 Git `975591d79efb4f5aa97c32e6a18a0b36a0e92b3a`、版本 0.1.25。交付时仅按提交规则递增版本到 0.1.26，并同步锁文件和宿主已安装 metadata；容器验收日志中的 0.1.25 是当时真实身份，不改写历史证据。没有修改正式 Agent、上下文、工具、Provider 或 TUI 执行逻辑，没有引入第二套解题引擎。

官方仓库为 `https://github.com/microsoft/SWE-bench-Live.git`，Python 判题分支固定到 commit `ad79b850f15e33992e96f03f6e97f05ddf9aa0be`，以 detached HEAD 使用且工作区干净。官方包版本为 swebench 4.0.3，隔离环境为 `/home/louishyh/.cache/novacode-evaluation/stage1/grader-venv`，Python 为 3.12.13。官方入口 `python -m swebench.harness.run_evaluation --help` 可启动，输出见 [grader-help.txt](grader-help.txt)，依赖精确版本见 [grader-requirements.lock](grader-requirements.lock) 和 [grader-installed.txt](grader-installed.txt)。没有在 NovaCode 的虚拟环境安装判题依赖。

固定数据为 `SWE-bench-Live/SWE-bench-Live` 的 verified，revision 为 `b51a86422e10cfd403beb4773e5a2947953e36ec`，完整文件有 500 条记录。Parquet SHA-256 为 `080e36e46198bf9c177a6b077624d4028baf6ff04d661c332cc1fe1e5dfa50b2`，与该 revision 文件的官方 LFS 元数据一致；下载发生超时后断点续传，完整指纹通过后才读取。完整数据和参考补丁留在项目外部缓存，未打包进 Agent 运行环境。

## 代表题与资源边界

代表候选选择 Faker 的 `joke2k__faker-2096`（目录 L03），理由是纯 Python 数值边界问题、CPU 可验收、不需要外部网络或模型。此次只核验该代表候选，未据 NovaCode 运行结果选择或替换题目，也未开始开发集或冻结集抽样。目标 base commit 为 `4240ef8fc783c0c1598085b596783ace9ccaee03`；任务行、参考补丁、官方测试脚本及各次日志的指纹见 [qualification/manifest.json](qualification/manifest.json)。

官方镜像固定为 `starryzhang/sweb.eval.x86_64.joke2k_1776_faker-2096@sha256:d0bedf38180bc7a970a9da180f4cfa234f24db7b1188b278f0d3b3a94b7fadb8`。核验脚本拒绝数据文件、官方 commit、镜像 digest 不一致或官方源码脏改动，不仅使用移动标签名。各次运行由同一 digest 创建全新容器，逐次检查 HEAD 和源码干净状态，单容器并发度为 1。

宿主调用者为 louishyh，任务容器使用官方镜像默认 root 身份；容器没有宿主目录挂载或 Docker socket，网络关闭。实际 cgroup 限制为 2 CPU、4294967296 字节内存、0 Swap、256 PID，判题超时为 600 秒。磁盘上限是共享宿主文件系统容量 134145380352 字节，代表题启动时可用 104531390464 字节；没有独立容器磁盘配额，不声称存在该配额。访问和 cgroup 实测见 [container-access.txt](container-access.txt) 及各次 resources.txt；这些值只适用于当前机器与此代表题，不能推断其余候选也可运行。

## 原始与参考判题

外层核验脚本复用固定官方代码的 `make_test_spec()`、`exec_run_with_timeout()` 和 `get_eval_report()`，执行官方生成的完整 eval.sh，未修改测试命令、隐藏测试或判分条件。原始运行不应用生产补丁，直接验证目标失败；参考运行在干净容器中应用原始参考补丁。使用这些官方函数处理无生产补丁的原始核验，避免将官方 rollout 入口对空补丁的应用失败冒充业务测试失败。

| 运行 | 官方 resolved | FAIL_TO_PASS | PASS_TO_PASS | 判题秒数 | cgroup 峰值内存 MiB |
|---|---|---|---|---:|---:|
| [original](qualification/original/report.json) | false | 指定目标失败 | 1959/1959 通过 | 48.34 | 121.7 |
| [reference-1](qualification/reference-1/report.json) | true | 1/1 通过 | 1959/1959 通过 | 50.33 | 120.0 |
| [reference-2](qualification/reference-2/report.json) | true | 1/1 通过 | 1959/1959 通过 | 48.42 | 120.8 |
| [reference-3](qualification/reference-3/report.json) | true | 1/1 通过 | 1959/1959 通过 | 47.55 | 120.2 |
| [reference-with-agent](qualification/reference-with-agent/report.json) | true | 1/1 通过 | 1959/1959 通过 | 48.10 | 210.8 |

指定目标为 `tests/providers/test_python.py::TestPydecimal::test_min_value_and_max_value_have_different_signs_return_evenly_distributed_values`。原始日志显示 1 failed、1961 passed、31 skipped；参考日志显示 1962 passed、31 skipped。31 个跳过项不在所需 FAIL_TO_PASS/PASS_TO_PASS 中；全部必需测试都实际通过。原始失败来自数值分布断言，非导入、收集或依赖错误。所有运行无超时、无 OOM，峰值来自内核 memory.peak，而非一次采样估计。完整测试输出和 CPU/内存记录保存在表中各运行目录的 test-output.txt、resources.txt。

## 独立 Agent 环境

在第五个全新容器中，将独立 CPython 3.12.13、uv 和离线 wheel 放到 `/opt/novacode/`，创建 `/opt/novacode/venv`，从项目锁文件导出的精确运行依赖安装 NovaCode。旧镜像使用 Python 3.8.20，特意提供 cryptography 的 manylinux_2_28 wheel；未改变依赖版本或为适配而覆盖目标环境。依赖清单及打包资产指纹见 [agent-requirements.txt](agent-requirements.txt)、[agent-runtime-manifest.json](agent-runtime-manifest.json)。

Agent 环境成功导入正式 Agent、ContextManager、ToolRunner、SessionService；六个现有工具完成写入、读取、编辑、路径搜索、内容搜索和目标解释器命令。它们均看到 `/testbed/nova-stage1-probe.txt`，检查后删除该文件；Bash 工具显示目标命令仍使用 Python 3.8.20，而 NovaCode 自身运行于 `/opt/novacode/venv/bin/python` 的 Python 3.12.13。实测输出见 [agent-smoke.txt](qualification/reference-with-agent/agent-smoke.txt)。

安装与工具检查前后，目标解释器路径、版本、pip freeze、Git HEAD 和工作区状态逐字节一致，见 [target-before.txt](qualification/reference-with-agent/target-before.txt)、[target-after.txt](qualification/reference-with-agent/target-after.txt)。随后同一容器参考判题通过，证明安装没有破坏目标依赖和工作区语义。这里是导入与真实工具机制检查，未创建模型 Provider，未运行模型驱动的解题对话或完整权限流程。

## 复跑方式

执行缓存位于 `/home/louishyh/.cache/novacode-evaluation/stage1/`；大数据文件、镜像层和运行环境 tar 不进入 Git。重建判题环境时从上述精确 commit 安装，使用已交付的 grader-requirements.lock 通过 `uv pip sync --python <隔离解释器>` 安装依赖，再 `uv pip install --no-deps -e <官方源码目录>`，检查官方入口可启动。NovaCode wheel 使用验收基线 0.1.25 源码构建；运行依赖按 agent-requirements.txt 下载到独立 wheelhouse，旧系统补充 manylinux_2_28 的 cryptography 49.0.0 wheel。

在项目根运行以下命令；output 必须是尚不存在的新目录，脚本不会覆盖已有核验记录。使用当前缓存时可以直接运行第二条；重建归档时先运行第一条。

```bash
.venv/bin/python evaluation/preflight/pack_agent_runtime.py \
  --python-home /home/louishyh/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu \
  --uv /home/louishyh/.local/bin/uv \
  --wheelhouse /home/louishyh/.cache/novacode-evaluation/stage1/wheels \
  --output /home/louishyh/.cache/novacode-evaluation/stage1/agent-runtime-replay.tar

/home/louishyh/.cache/novacode-evaluation/stage1/grader-venv/bin/python \
  evaluation/preflight/verify_live_environment.py \
  --dataset /home/louishyh/.cache/novacode-evaluation/stage1/verified.parquet \
  --output /home/louishyh/.cache/novacode-evaluation/stage1/qualification-replay \
  --agent-archive /home/louishyh/.cache/novacode-evaluation/stage1/agent-runtime-replay.tar
```

原始核验目录为缓存中的 qualification-1，交付只复制判题报告、日志和工具证据。隐藏参考补丁和 eval.sh 在外部目录保存，其指纹记录在 manifest 中；不会将这些资产传给执行工具检查的 Agent 环境，只有工具检查结束后才由外部判题进程应用。重建 tar 的打包时间元数据可能改变，需记录新 tar 指纹，不能冒充原归档字节一致。

## 质量门禁与完成边界

项目当前完整 pytest 为 725 passed，详见 [pytest.txt](pytest.txt)。Ruff lint/format 通过；运行时边界门禁 mypy 180/180、C901 21/21，均无新增诊断，详见 [boundaries.txt](boundaries.txt)。新增 preflight 脚本以真实代表题原始失败和四次参考成功验证，未用 Fake 或测试替身代替容器判题。所有创建的核验容器已清理，证据指纹、版本一致性、锁文件、OpenSpec 严格校验及 Git 差异检查的最终结果见 [release-validation.txt](release-validation.txt)；交付文件指纹见 [delivery-fingerprints.sha256](delivery-fingerprints.sha256)。

仅完成 ENV_READY，没有完成 CODE_READY、DEV_ADMITTED、先导、冻结或正式实验。当前只核验 1 个 Live 代表候选，未将 500 条候选或 12 个目录条目称为已入库题库。未发起模型调用；真实 tmux 对话与自动化入口一致性、有限模型预算和真实运行在后续对应阶段实施，本次没有产品功能变更，不提前进行付费对话或将工具检查当作模型成功率。全过程未读取、审查或修改项目 docs/，未修改用户长期记忆或不相关工作区。
