# 开发专项 S01–S12

本目录包含公开 fixture 构建器、外部判题器与专项机制检查。`assets/contracts/` 是版本化任务合同，`assets/public/` 只包含初始源码、业务记录和固定请求；`assets/external/` 保存答案集合、正确/缺陷实现和资格证据。运行器只把对应公开初始文件复制到全新容器的 `/specialty-bed`，不会复制本目录、完整仓库或外部验收资产。NovaCode wheel 也不包含构建器和判题资产。

S01/S02 的回答格式为 `{"files": ["相对路径"], "symbols": ["路径:符号"], "relations": [["源", "目标"]]}`。不同合法路径预先列入外部验收集合；自由解释不计语义分。S03/S04 仅允许新增 `tests/test_added.py`，使用标准库 unittest。独立判题要求正确实现通过、目标行为实际执行、全部固定缺陷产生新增测试断言失败；导入/收集错误、跳过与生产修改不能冒充检错。

S05 的第一次有效 grep 执行收到固定搜索错误。S06 的第一次标准命令 `python -m unittest discover -s tests -v` 启动真实进程树，经子进程握手后取消并检查停止；后续调用执行原命令。故障发生于正常权限与 Hook 检查之后，规则、参数指纹及清理状态写入原账本；绕过指定调用而合法完成时，故障覆盖为零。S07–S09 固定脚本分别为 6、6、7 条请求，独立运行不继承其他配置的对话或摘要；压力尺寸只是输入身份，实际卸载/摘要由原账本另报。

S10 使用非基础内置 CI 工具，S11 使用职责相近的本地 stdio MCP 测试/部署查询，S12 混合内置 issue 与 MCP 提交查询。业务数据同时存在公开 records.json 中，允许通过合法基础工具完成；评分检查最终事实和行为，发现覆盖独立计量。三个策略使用同一业务数据、能力和授权集合，MCP 在首个模型调用前连接，运行结束关闭。

重建公开/隐藏资产使用 `.venv/bin/python evaluation/specialty/build.py --output <全新目录> --image <固定镜像digest>`；入库使用 `.venv/bin/python evaluation/specialty/validate.py --assets <资产目录> --output <全新核验目录>`。二者不调用模型，后者逐题运行离线、无宿主挂载的干净判题容器；每个容器为 1 CPU、512 MiB 内存、64 PID，执行超时 20 秒。当前固定镜像为 `starryzhang/sweb.eval.x86_64.joke2k_1776_faker-2096@sha256:d0bedf38180bc7a970a9da180f4cfa234f24db7b1188b278f0d3b3a94b7fadb8`，它只提供 Python/Git 环境，自建任务没有使用 Faker 题目合同或官方成绩。

任务合同绑定公开包和验收清单 SHA-256；验收清单进一步绑定 judge、test_driver、build 与 definitions 的字节指纹。判题实现变化必须重新入库。环境核验只检查解释器和源码可编译，预设缺陷造成的原始业务失败另列；正确/负例均在独立干净容器执行。资格与 SELECT/ADMIT 账本按任务指纹关联，核验输出使用新身份，旧失败记录保留。

`run.py` 会调用真实模型，只用于已授权、有界的 S05 tmux/S12 自动代表验收；`review.py` 只读复核并无损导出账本、补丁、屏幕及原始 worker 产物。已入库清单、复跑命令和证据边界见 [第七阶段报告](../../openspec/changes/add-reproducible-agent-evaluation/evidence/stage-7/specialty-validation.md)。本阶段只交付 12 道开发专项；完整 24 道开发集、先导校准、冻结题与正式统计仍待后续阶段。

2026-10-10 第八阶段修正了事实答案解析：仅包含合同字段的完整 JSON 对象进入答案冲突检查，解释中的配置示例不计候选答案。公开 fixture、输入和环境字节未变；全部 12 题已重新核验，当前资格文件为 `assets/external/Sxx/qualification-specialty-admission.json`，当前选择账本为 `openspec/changes/add-reproducible-agent-evaluation/evidence/stage-8/specialty-admission/selection.jsonl`。旧资产与旧判题源码原字节保留在第八阶段 `specialty-before-parser-fix.tar.gz`，旧第七阶段账本保留历史身份。
