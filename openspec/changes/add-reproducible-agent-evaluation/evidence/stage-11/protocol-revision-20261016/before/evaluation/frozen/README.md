# 冻结题库与执行身份

本目录完成第十阶段的离线入库、冻结与计划生成，状态 FROZEN_VALID。24 道固定版本 SWE-bench-Live 任务覆盖 13 个仓库，另有 24 道独立专项；两来源分别满足 8/12/4 难度，专项类别为 4/4/4/6/6。实际清单包含 48 个冻结合同、432 个 PLANNED 原计划，真实模型调用为零；见 [冻结验收报告](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-10/freeze-validation.md)。开发任务只用于家族隔离审计，不进入冻结计划。停止在第十一阶段之前。

`live-selection-rule.json` 在首次资格验收前固定完整 500 行题池、种子、仓库范围与上限、开发问题键、已留证的无效候选和认知审查。后续同层审查与替补保留资格证据，不能依据 NovaCode 成败选题。Live 必须原始目标失败、原有回归通过、参考连续三次通过；最终安装的 Agent 运行时另行逐镜像核验，不改变项目解释器与依赖。

专项的 `definitions.py` 和 `specialty-assets/external/` 只由外部建设与判题进程读取。Agent 仅收到固定公开脚本、初始文件和公开工具业务记录；隐藏验收与参考修复不进入产品 wheel 或任务容器。`judge.py` 复用现有事实和行为判题，补测题根据显式被测符号核验正确实现与全部缺陷，拒绝收集错误、跳过、无条件失败和范围越界。

`freeze.py build` 校验完整题库、部署证据、源码/依赖/官方判题/镜像/参数/权限/Hook/记忆/工具/预算和统计身份，生成 `campaign/manifest.json`、标准 Campaign 合同与 432 个 PLANNED 原计划。每题三配置各重复三次；每来源分别均衡六种配置顺序，单任务并发度为 1。没有真实 Provider 执行命令，冻结步骤模型调用为零。

```bash
.venv/bin/python evaluation/frozen/freeze.py verify --output evaluation/frozen/campaign
.venv/bin/python evaluation/frozen/audit.py --manifest evaluation/frozen/campaign/manifest.json --output /新的目录/identity-audit.json
```

正式入口仍是已有 `run_task()`，冻结任务在其前置阶段核验清单锚点、原计划、全部源码和资产、实际参数与剩余总预算。清单、代码、SDK、验收、工具或预算变化会阻止模型调用；改变版本必须创建新身份。清单绑定本机外部运行时与官方判题文件，迁移机器需要重新核验，不能保留失效路径或指纹。源码身份使用发布前 base commit 与实际执行文件树，提交生成的新 commit 不改写冻结身份。

Windows Registry 通道只用于宿主镜像准备：沿用 Docker 登录，网络路由在每个准备身份中记录为既有本地代理或 Windows 直连，认证内容不入库；大文件超时前缀交给既有 `.part/Range` 恢复，完整大小、SHA-256、导入 digest 与 rootfs 均须通过。新生成的传输 tar 在核验导入后释放，已留证的资格与失败不删改。

原生和 Windows curl 均可用 `NOVACODE_REGISTRY_TEMP` 指定临时目录。当前准备身份将其设为 `/home/louishyh/.cache/novacode-evaluation/registry-temp`，实际存储在 `F:\WSL\Ubuntu-22.04\ext4.vhdx` 内；Windows curl 仅负责网络，二进制 stdout 由 WSL 写入该目录，响应头通过同目录的 UNC 路径传递。此前 F 宿主目录通道与校验记录保留；不要恢复为 C 盘默认 Windows Temp。下载超时或中断会停止所属 Linux 准备进程组。存储诊断、实际下载核验和缓存迁移见 [storage-diagnosis.md](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-10/storage-diagnosis.md)。恢复脚本只启动资格控制器，不执行真实模型实验或自动提交。

`tmux_probe.py` 使用脚本化 Provider 检查正式 TUI、工具调用、回复、Session 与退出，是无模型的产品链机制证据。冻结阶段不会自动开始第十一阶段的真实配对实验。远端权重无法不可变固定、推理子计数及实际账单未知、先导无 Layer2 触发的限制保留在冻结清单中。
